@description('Name of the Connector Namespace.')
param name string

param location string = resourceGroup().location
param tags object = {}

@description('Object ID of the Function app user-assigned managed identity that invokes the MCP server.')
param functionPrincipalId string

@description('Object ID of the deploying user for local MCP testing. Empty skips the access policy.')
param developerPrincipalId string = ''

@description('Entra tenant ID used by connection access policies.')
param tenantId string = tenant().tenantId

@description('Storage account containing the policy documents.')
param storageAccountName string

@description('Blob container containing the policy documents.')
param policyContainerName string

@description('Name of the Azure Blob connection.')
param connectionName string = 'blob-policy-reader'

@description('Name of the configurable MCP server.')
param mcpServerName string = 'Blob-policy-reader'

var storageBlobDataReaderRoleDefinitionId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'

resource connectorNamespace 'Microsoft.Web/connectorGateways@2026-05-01-preview' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

resource blobConnection 'Microsoft.Web/connectorGateways/connections@2026-05-01-preview' = {
  parent: connectorNamespace
  name: connectionName
  properties: {
    connectorName: 'azureblob'
    displayName: 'Expense Policy Blob Reader'
    parameterValueSet: {
      name: 'managedIdentityAuth'
      values: {}
    }
  }
}

resource namespaceConnectionAccessPolicy 'Microsoft.Web/connectorGateways/connections/accessPolicies@2026-05-01-preview' = {
  parent: blobConnection
  name: 'namespace-policy-reader'
  location: location
  properties: {
    principal: {
      type: 'ActiveDirectory'
      identity: {
        objectId: connectorNamespace.identity.principalId
        tenantId: tenantId
      }
    }
  }
}

resource functionConnectionAccessPolicy 'Microsoft.Web/connectorGateways/connections/accessPolicies@2026-05-01-preview' = {
  parent: blobConnection
  name: 'function-policy-reader'
  location: location
  properties: {
    principal: {
      type: 'ActiveDirectory'
      identity: {
        objectId: functionPrincipalId
        tenantId: tenantId
      }
    }
  }
}

resource developerConnectionAccessPolicy 'Microsoft.Web/connectorGateways/connections/accessPolicies@2026-05-01-preview' = if (!empty(developerPrincipalId)) {
  parent: blobConnection
  name: 'developer-policy-reader'
  location: location
  properties: {
    principal: {
      type: 'ActiveDirectory'
      identity: {
        objectId: developerPrincipalId
        tenantId: tenantId
      }
    }
  }
}

resource policyMcpServer 'Microsoft.Web/connectorGateways/mcpServerConfigs@2026-05-01-preview' = {
  parent: connectorNamespace
  name: mcpServerName
  properties: {
    description: 'Read-only access to expense policy documents in Azure Blob Storage.'
    state: 'Enabled'
    disableApiKeyAuth: true
    settings: {
      textOnlyContent: true
    }
    policies: []
    connectors: [
      {
        connectionName: connectionName
        name: 'azureblob'
        displayName: 'Azure Blob Storage'
        description: 'Lists and reads expense policy documents.'
        operations: [
          {
            name: 'ListFolder_V4'
            displayName: 'List expense policy documents'
            description: 'List the expense policy documents.'
            userParameters: [
              {
                name: 'dataset'
                value: storageAccountName
              }
              {
                name: 'id'
                value: policyContainerName
              }
            ]
            agentParameters: []
          }
          {
            name: 'GetFileContentByPath_V2'
            displayName: 'Read expense policy document'
            description: 'Read one Markdown expense policy document by its full Blob path.'
            userParameters: [
              {
                name: 'dataset'
                value: storageAccountName
              }
            ]
            agentParameters: [
              {
                name: 'path'
                schema: {
                  type: 'string'
                  required: true
                  description: 'Full Blob path, for example /${policyContainerName}/travel-policy.md.'
                }
              }
            ]
          }
        ]
      }
    ]
  }
  dependsOn: [
    namespaceConnectionAccessPolicy
    functionConnectionAccessPolicy
    developerConnectionAccessPolicy
  ]
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storageAccount
  name: 'default'
}

resource policyContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: policyContainerName
}

resource connectorPolicyReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(policyContainer.id, connectorNamespace.name, storageBlobDataReaderRoleDefinitionId)
  scope: policyContainer
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      storageBlobDataReaderRoleDefinitionId
    )
    principalId: connectorNamespace.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output mcpServerUrl string = policyMcpServer.properties.mcpEndpointUrl
output connectorNamespaceName string = connectorNamespace.name
output connectionName string = blobConnection.name
output principalId string = connectorNamespace.identity.principalId
