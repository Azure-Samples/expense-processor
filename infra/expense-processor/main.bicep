@minLength(1)
@maxLength(64)
@description('Name of the environment which is used to generate a short unique hash used in all resources.')
param environmentName string

@minLength(1)
@description('Primary location for all resources. Must support Azure Functions Flex Consumption and the default Microsoft Foundry gpt-5.4 Global Standard deployment.')
@allowed([
  'centralus'
  'eastus'
  'eastus2'
  'northcentralus'
  'southcentralus'
  'westus'
])
@metadata({
  azd: {
    type: 'location'
  }
})
param location string

@description('Microsoft Foundry model deployment name.')
param foundryModel string = 'gpt-5.4'

@description('Microsoft Foundry model name.')
param foundryModelName string = 'gpt-5.4'

@description('Microsoft Foundry model version.')
param foundryModelVersion string = '2026-03-05'

@description('Microsoft Foundry deployment capacity.')
param foundryDeploymentCapacity int = 50

@description('Reasoning effort for supported Foundry reasoning models.')
@allowed([
  'none'
  'low'
  'medium'
  'high'
  'xhigh'
])
param reasoningEffort string = 'medium'

@description('Reasoning summary mode for supported Foundry reasoning models.')
@allowed([
  'auto'
  'concise'
  'detailed'
])
param reasoningSummary string = 'concise'

@description('Object ID of the user/principal running the deployment. Granted Storage Queue Data Contributor so the demo scripts (scripts/send_expense.py, scripts/read_decision.py) can send test requests to the input queue and read routed decisions from the output queues. Populated by azd from AZURE_PRINCIPAL_ID.')
param principalId string = ''

param resourceToken string

var abbrs = loadJsonContent('../abbreviations.json')
var tags = { 'azd-env-name': environmentName }
var functionAppName = '${abbrs.webSitesFunctions}expense-skill-${resourceToken}'
var storageAccountName = '${abbrs.storageStorageAccounts}${resourceToken}'
var foundryAccountName = 'cog-${resourceToken}'
var foundryProjectName = '${foundryAccountName}-proj'
var aiGatewayName = 'apim-expense-skill-${resourceToken}'
var deploymentStorageContainerName = 'app-package-${take(functionAppName, 32)}-${take(toLower(uniqueString(functionAppName, resourceToken)), 7)}'
var connectorNamespaceName = 'cns-expense-skill-${resourceToken}'

var inputQueueName = 'expense-requests'
var outputQueueNames = [
  'expense-approved'
  'expense-review'
  'expense-flagged'
]
var allQueueNames = union([inputQueueName], outputQueueNames)

// Blob container that holds the expense-approval policy documents the skill chooses among at
// decision time. Deployment seeds a general policy plus category-specific documents.
var policyContainerName = 'policies'

// User Assigned Managed Identity
module apiUserAssignedIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.4.1' = {
  name: 'apiUserAssignedIdentity'
  params: {
    location: location
    tags: tags
    name: '${abbrs.managedIdentityUserAssignedIdentities}expense-skill-${resourceToken}'
  }
}

// Microsoft Foundry
module foundry './foundry.bicep' = {
  name: 'foundry'
  params: {
    accountName: foundryAccountName
    projectName: foundryProjectName
    location: location
    tags: tags
    modelDeploymentName: foundryModel
    modelName: foundryModelName
    modelVersion: foundryModelVersion
    deploymentCapacity: foundryDeploymentCapacity
  }
}

// API Management AI Gateway governs all deployed model calls. The Function authenticates to the
// gateway with its UAMI; APIM uses its own system-assigned identity for the Foundry backend.
module aiGateway './ai-gateway.bicep' = {
  name: 'aiGateway'
  params: {
    name: aiGatewayName
    location: location
    tags: tags
    tenantId: tenant().tenantId
    functionClientId: apiUserAssignedIdentity.outputs.clientId
    foundryAccountName: foundry.outputs.accountName
    foundryOpenAiEndpoint: foundry.outputs.openAiEndpoint
    contentSafetyEndpoint: foundry.outputs.contentSafetyEndpoint
    applicationInsightsName: monitoring.outputs.name
    modelDeploymentName: foundry.outputs.modelDeploymentName
  }
}

// App Service Plan (Flex Consumption)
module appServicePlan 'br/public:avm/res/web/serverfarm:0.1.1' = {
  name: 'appserviceplan'
  params: {
    name: '${abbrs.webServerFarms}${resourceToken}'
    sku: {
      name: 'FC1'
      tier: 'FlexConsumption'
    }
    reserved: true
    location: location
    tags: tags
  }
}

// Function App
module api '../common/function-app.bicep' = {
  name: 'api'
  params: {
    name: functionAppName
    location: location
    tags: tags
    applicationInsightsName: monitoring.outputs.name
    appServicePlanId: appServicePlan.outputs.resourceId
    runtimeName: 'python'
    runtimeVersion: '3.13'
    storageAccountName: storage.outputs.name
    deploymentStorageContainerName: deploymentStorageContainerName
    identityId: apiUserAssignedIdentity.outputs.resourceId
    identityClientId: apiUserAssignedIdentity.outputs.clientId
    appSettings: {
      AZURE_FUNCTIONS_AGENTS_PROVIDER: 'azure_openai'
      AZURE_OPENAI_ENDPOINT: aiGateway.outputs.gatewayUrl
      AZURE_OPENAI_DEPLOYMENT: foundry.outputs.modelDeploymentName
      AZURE_FUNCTIONS_AGENTS_REASONING_EFFORT: reasoningEffort
      AZURE_FUNCTIONS_AGENTS_REASONING_SUMMARY: reasoningSummary
      AZURE_CLIENT_ID: apiUserAssignedIdentity.outputs.clientId
      // OUTPUT_STORAGE_ACCOUNT is exported for the operator-facing demo scripts.
      OUTPUT_STORAGE_ACCOUNT: storage.outputs.name
      // The hosted skill calls both least-privilege storage MCP servers with its UAMI.
      POLICY_MCP_SERVER_URL: policyConnector.outputs.policyMcpServerUrl
      POLICY_MCP_CLIENT_ID: apiUserAssignedIdentity.outputs.clientId
      QUEUE_MCP_SERVER_URL: policyConnector.outputs.queueMcpServerUrl
      QUEUE_MCP_CLIENT_ID: apiUserAssignedIdentity.outputs.clientId
      ENABLE_MULTIPLATFORM_BUILD: 'true'
      PYTHON_ENABLE_INIT_INDEXING: '1'
    }
  }
}

// Storage Account
module storage 'br/public:avm/res/storage/storage-account:0.8.3' = {
  name: 'storage'
  params: {
    name: storageAccountName
    allowBlobPublicAccess: false
    // Shared-key (local auth) is disabled to satisfy the enforced org policy
    // "Azure Storage Policy to disable local auth" (Deny). The Function app uses
    // managed identity for AzureWebJobsStorage + deployment storage, so no
    // shared-key access is needed.
    allowSharedKeyAccess: false
    dnsEndpointType: 'Standard'
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    blobServices: {
      containers: [
        { name: deploymentStorageContainerName }
        { name: policyContainerName }
      ]
    }
    minimumTlsVersion: 'TLS1_2'
    location: location
    tags: tags
  }
}

// Input + per-outcome output queues
module storageQueues './storage-queues.bicep' = {
  name: 'storageQueues'
  params: {
    storageAccountName: storage.outputs.name
    queueNames: allQueueNames
  }
}

// Least-privilege policy reads and decision writes through managed-identity storage MCP connectors.
module policyConnector './blob-policy-connector.bicep' = {
  name: 'policyConnector'
  params: {
    name: connectorNamespaceName
    location: location
    tags: tags
    functionPrincipalId: apiUserAssignedIdentity.outputs.principalId
    developerPrincipalId: principalId
    storageAccountName: storage.outputs.name
    policyContainerName: policyContainerName
    outputQueueNames: outputQueueNames
  }
  dependsOn: [
    storageQueues
  ]
}

// RBAC — storage data roles + app insights metrics publisher for the function identity
module rbac '../common/host-rbac.bicep' = {
  name: 'rbacAssignments'
  params: {
    storageAccountName: storage.outputs.name
    appInsightsName: monitoring.outputs.name
    managedIdentityPrincipalId: apiUserAssignedIdentity.outputs.principalId
    // Grant the deployer data-plane queue access so the demo scripts can send test
    // requests to the input queue and read decisions from the output queues.
    developerPrincipalId: principalId
  }
}

module monitoring '../common/monitoring.bicep' = {
  name: 'processorMonitoring'
  params: {
    applicationInsightsName: '${abbrs.insightsComponents}${resourceToken}'
    workspaceName: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
    location: location
    tags: tags
  }
}

// Outputs
output AZURE_LOCATION string = location
output AZURE_FUNCTION_NAME string = api.outputs.SERVICE_API_NAME
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output FOUNDRY_MODEL string = foundry.outputs.modelDeploymentName
output AI_GATEWAY_NAME string = aiGateway.outputs.name
output AI_GATEWAY_URL string = aiGateway.outputs.gatewayUrl
output AI_GATEWAY_RESPONSES_ENDPOINT string = aiGateway.outputs.responsesEndpoint
output OUTPUT_STORAGE_ACCOUNT string = storage.outputs.name
output INPUT_QUEUE_NAME string = inputQueueName
output POLICY_MCP_SERVER_URL string = policyConnector.outputs.policyMcpServerUrl
output QUEUE_MCP_SERVER_URL string = policyConnector.outputs.queueMcpServerUrl
output POLICY_CONNECTOR_NAMESPACE_NAME string = policyConnector.outputs.connectorNamespaceName
output POLICY_CONNECTOR_CONNECTION_NAME string = policyConnector.outputs.blobConnectionName
output QUEUE_CONNECTOR_CONNECTION_NAME string = policyConnector.outputs.queueConnectionName
output APPLICATIONINSIGHTS_RESOURCE_ID string = monitoring.outputs.resourceId
output EXPENSE_QUEUE_SERVICE_URI string = storageQueues.outputs.queueServiceUri
output OUTPUT_QUEUE_NAMES array = outputQueueNames
