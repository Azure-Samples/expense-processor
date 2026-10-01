param name string
param location string = resourceGroup().location
param tags object = {}
param tenantId string
param functionClientId string
param foundryAccountName string
param foundryOpenAiEndpoint string
param contentSafetyEndpoint string
param applicationInsightsName string
param modelDeploymentName string
param publisherName string = 'Azure Samples'
param publisherEmail string = 'azure-samples@microsoft.com'

var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var monitoringMetricsPublisherRoleId = '3913510d-42f4-4e42-8a64-420c390055eb'
var openAiBackendId = 'foundry-openai'
var contentSafetyBackendId = 'foundry-content-safety'
var apiId = 'azure-openai-responses'
var loggerId = 'application-insights'
var cognitiveServicesAudience = 'https://cognitiveservices.azure.com'
var openAiBackendUrl = '${foundryOpenAiEndpoint}openai'
var policyTemplate = loadTextContent('./ai-gateway-policy.xml')
var policyWithTenant = replace(policyTemplate, '__TENANT_ID__', tenantId)
var policyWithClient = replace(policyWithTenant, '__FUNCTION_CLIENT_ID__', functionClientId)
var gatewayPolicy = replace(policyWithClient, '__MODEL_DEPLOYMENT_NAME__', modelDeploymentName)

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' existing = {
  name: foundryAccountName
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

resource apiManagement 'Microsoft.ApiManagement/service@2024-06-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Developer'
    capacity: 1
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherName: publisherName
    publisherEmail: publisherEmail
    publicNetworkAccess: 'Enabled'
    virtualNetworkType: 'None'
  }
}

resource openAiBackend 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  parent: apiManagement
  name: openAiBackendId
  properties: {
    title: 'Microsoft Foundry OpenAI'
    description: 'Existing Microsoft Foundry OpenAI endpoint'
    protocol: 'http'
    url: openAiBackendUrl
    tls: {
      validateCertificateChain: true
      validateCertificateName: true
    }
  }
}

resource contentSafetyBackend 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  parent: apiManagement
  name: contentSafetyBackendId
  // APIM can reject concurrent child updates while its service container is activating.
  dependsOn: [
    openAiBackend
  ]
  properties: {
    title: 'Microsoft Foundry Content Safety'
    description: 'Existing Microsoft Foundry Content Safety endpoint'
    protocol: 'http'
    url: contentSafetyEndpoint
    credentials: {
      #disable-next-line BCP037
      managedIdentity: {
        resource: cognitiveServicesAudience
      }
    }
    tls: {
      validateCertificateChain: true
      validateCertificateName: true
    }
  }
}

resource responsesApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apiManagement
  name: apiId
  dependsOn: [
    contentSafetyBackend
  ]
  properties: {
    apiType: 'http'
    type: 'http'
    displayName: 'Azure OpenAI Responses API'
    description: 'Governed Responses API used by the expense processor hosted skill'
    path: 'openai'
    protocols: [
      'https'
    ]
    subscriptionRequired: false
  }
}

resource responsesOperation 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: responsesApi
  name: 'create-response'
  properties: {
    displayName: 'Create model response'
    description: 'Creates a response with the configured Microsoft Foundry model deployment'
    method: 'POST'
    urlTemplate: '/v1/responses'
    request: {
      headers: [
        {
          name: 'Content-Type'
          required: true
          type: 'string'
        }
      ]
    }
    responses: [
      {
        statusCode: 200
        description: 'Model response'
      }
    ]
  }
}

resource responsesPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = {
  parent: responsesApi
  name: 'policy'
  dependsOn: [
    responsesOperation
  ]
  properties: {
    format: 'rawxml'
    value: gatewayPolicy
  }
}

resource applicationInsightsLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' = {
  parent: apiManagement
  name: loggerId
  dependsOn: [
    responsesPolicy
  ]
  properties: {
    loggerType: 'applicationInsights'
    description: 'Managed-identity Application Insights logger for AI Gateway telemetry'
    resourceId: applicationInsights.id
    credentials: {
      connectionString: applicationInsights.properties.ConnectionString
      identityClientId: 'SystemAssigned'
    }
  }
}

resource apiDiagnostic 'Microsoft.ApiManagement/service/apis/diagnostics@2024-06-01-preview' = {
  parent: responsesApi
  name: 'applicationinsights'
  properties: {
    loggerId: applicationInsightsLogger.id
    alwaysLog: 'allErrors'
    httpCorrelationProtocol: 'W3C'
    logClientIp: false
    metrics: true
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    verbosity: 'information'
  }
}

resource foundryCognitiveServicesUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundryAccount.id, apiManagement.id, cognitiveServicesUserRoleId)
  scope: foundryAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: apiManagement.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource applicationInsightsMetricsPublisherRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(applicationInsights.id, apiManagement.id, monitoringMetricsPublisherRoleId)
  scope: applicationInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringMetricsPublisherRoleId)
    principalId: apiManagement.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output name string = apiManagement.name
output gatewayUrl string = apiManagement.properties.gatewayUrl
output responsesEndpoint string = '${apiManagement.properties.gatewayUrl}/openai/v1/responses'
output principalId string = apiManagement.identity.principalId
