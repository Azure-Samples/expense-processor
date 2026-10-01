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

@description('Output queues the expense processor may route decisions to.')
param outputQueueNames array

@description('Name of the Azure Blob connection.')
param blobConnectionName string = 'blob-policy-reader'

@description('Name of the Azure Queues connection.')
param queueConnectionName string = 'queue-decision-writer'

@description('Name of the Blob policy MCP server.')
param policyMcpServerName string = 'Blob-policy-reader'

@description('Name of the queue-routing MCP server.')
param queueMcpServerName string = 'Queue-decision-writer'

var storageBlobDataReaderRoleDefinitionId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var storageQueueDataMessageSenderRoleDefinitionId = 'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a'
var queueStorageEndpoint = 'https://${storageAccountName}.queue.${environment().suffixes.storage}/'

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
  name: blobConnectionName
  properties: {
    connectorName: 'azureblob'
    displayName: 'Expense Policy Blob Reader'
    parameterValueSet: {
      name: 'managedIdentityAuth'
      values: {}
    }
  }
}

resource queueConnection 'Microsoft.Web/connectorGateways/connections@2026-05-01-preview' = {
  parent: connectorNamespace
  name: queueConnectionName
  properties: {
    connectorName: 'azurequeues'
    displayName: 'Expense Decision Queue Writer'
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

resource namespaceQueueConnectionAccessPolicy 'Microsoft.Web/connectorGateways/connections/accessPolicies@2026-05-01-preview' = {
  parent: queueConnection
  name: 'namespace-decision-writer'
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

resource functionQueueConnectionAccessPolicy 'Microsoft.Web/connectorGateways/connections/accessPolicies@2026-05-01-preview' = {
  parent: queueConnection
  name: 'function-decision-writer'
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

resource developerQueueConnectionAccessPolicy 'Microsoft.Web/connectorGateways/connections/accessPolicies@2026-05-01-preview' = if (!empty(developerPrincipalId)) {
  parent: queueConnection
  name: 'developer-decision-writer'
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
  name: policyMcpServerName
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
        connectionName: blobConnectionName
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

resource queueMcpServer 'Microsoft.Web/connectorGateways/mcpServerConfigs@2026-05-01-preview' = {
  parent: connectorNamespace
  name: queueMcpServerName
  properties: {
    description: 'Send expense decisions to the approved Azure Queue Storage output queues.'
    state: 'Enabled'
    disableApiKeyAuth: true
    settings: {
      textOnlyContent: true
    }
    policies: []
    connectors: [
      {
        connectionName: queueConnectionName
        name: 'azurequeues'
        displayName: 'Azure Queue Storage'
        description: 'Routes one expense decision to an approved output queue.'
        operations: [
          {
            name: 'PutMessage_V2'
            displayName: 'Route expense decision'
            description: 'Send one expense decision JSON message to the selected output queue.'
            userParameters: [
              {
                name: 'storageAccountName'
                value: queueStorageEndpoint
              }
            ]
            agentParameters: [
              {
                name: 'queueName'
                schema: {
                  type: 'string'
                  required: true
                  enum: outputQueueNames
                  description: 'Destination queue for the decision.'
                }
              }
              {
                name: 'message'
                schema: {
                  type: 'string'
                  required: true
                  description: 'Complete expense decision as a compact JSON string.'
                }
              }
            ]
          }
        ]
      }
    ]
  }
  dependsOn: [
    namespaceQueueConnectionAccessPolicy
    functionQueueConnectionAccessPolicy
    developerQueueConnectionAccessPolicy
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

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' existing = {
  parent: storageAccount
  name: 'default'
}

resource outputQueues 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' existing = [
  for queueName in outputQueueNames: {
    parent: queueService
    name: queueName
  }
]

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

resource connectorQueueSenderRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (queueName, index) in outputQueueNames: {
    name: guid(outputQueues[index].id, connectorNamespace.name, storageQueueDataMessageSenderRoleDefinitionId)
    scope: outputQueues[index]
    properties: {
      roleDefinitionId: subscriptionResourceId(
        'Microsoft.Authorization/roleDefinitions',
        storageQueueDataMessageSenderRoleDefinitionId
      )
      principalId: connectorNamespace.identity.principalId
      principalType: 'ServicePrincipal'
    }
  }
]

output policyMcpServerUrl string = policyMcpServer.properties.mcpEndpointUrl
output queueMcpServerUrl string = queueMcpServer.properties.mcpEndpointUrl
output connectorNamespaceName string = connectorNamespace.name
output blobConnectionName string = blobConnection.name
output queueConnectionName string = queueConnection.name
output principalId string = connectorNamespace.identity.principalId
