targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Environment name used for resource groups and unique resource names.')
param environmentName string

@minLength(1)
@description('Region supporting Azure Functions Flex Consumption and the configured Foundry model.')
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

param foundryModel string = 'gpt-5.4'
param foundryModelName string = 'gpt-5.4'
param foundryModelVersion string = '2026-03-05'
param foundryDeploymentCapacity int = 50

@allowed([
  'none'
  'low'
  'medium'
  'high'
  'xhigh'
])
param reasoningEffort string = 'medium'

@allowed([
  'auto'
  'concise'
  'detailed'
])
param reasoningSummary string = 'concise'

@description('Deploying principal granted queue and policy access for the operator scripts and local development.')
param principalId string = ''

var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = { 'azd-env-name': environmentName }

resource processorGroup 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: '${abbrs.resourcesResourceGroups}${environmentName}'
  location: location
  tags: tags
}

resource mcpGroup 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: '${abbrs.resourcesResourceGroups}${environmentName}-mcp'
  location: location
  tags: tags
}

module processor './expense-processor/main.bicep' = {
  name: 'expenseProcessor'
  scope: processorGroup
  params: {
    environmentName: environmentName
    resourceToken: resourceToken
    location: location
    principalId: principalId
    foundryModel: foundryModel
    foundryModelName: foundryModelName
    foundryModelVersion: foundryModelVersion
    foundryDeploymentCapacity: foundryDeploymentCapacity
    reasoningEffort: reasoningEffort
    reasoningSummary: reasoningSummary
  }
}

module expenseMcp './expense-mcp/main.bicep' = {
  name: 'expenseMcp'
  scope: mcpGroup
  params: {
    name: '${abbrs.webSitesFunctions}expense-mcp-${resourceToken}'
    resourceToken: resourceToken
    location: location
    tags: tags
    expenseQueueServiceUri: processor.outputs.EXPENSE_QUEUE_SERVICE_URI
  }
}

// Only queue configuration and scoped access cross the app resource-group boundary.
module queueAccess './integration/queue-access.bicep' = {
  name: 'expenseMcpQueueAccess'
  scope: processorGroup
  params: {
    expenseStorageAccountName: processor.outputs.OUTPUT_STORAGE_ACCOUNT
    inputQueueName: processor.outputs.INPUT_QUEUE_NAME
    outputQueueNames: processor.outputs.OUTPUT_QUEUE_NAMES
    principalId: expenseMcp.outputs.principalId
  }
}

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = processorGroup.name
output EXPENSE_PROCESSOR_RESOURCE_GROUP string = processorGroup.name
output EXPENSE_MCP_RESOURCE_GROUP string = mcpGroup.name
output AZURE_FUNCTION_NAME string = processor.outputs.AZURE_FUNCTION_NAME
output EXPENSE_MCP_FUNCTION_NAME string = expenseMcp.outputs.name
output EXPENSE_MCP_SERVER_URL string = expenseMcp.outputs.endpoint
output EXPENSE_PROCESSOR_APPLICATIONINSIGHTS_RESOURCE_ID string = processor.outputs.APPLICATIONINSIGHTS_RESOURCE_ID
output EXPENSE_MCP_APPLICATIONINSIGHTS_RESOURCE_ID string = expenseMcp.outputs.applicationInsightsResourceId
output FOUNDRY_PROJECT_ENDPOINT string = processor.outputs.FOUNDRY_PROJECT_ENDPOINT
output FOUNDRY_MODEL string = processor.outputs.FOUNDRY_MODEL
output AI_GATEWAY_NAME string = processor.outputs.AI_GATEWAY_NAME
output AI_GATEWAY_URL string = processor.outputs.AI_GATEWAY_URL
output AI_GATEWAY_RESPONSES_ENDPOINT string = processor.outputs.AI_GATEWAY_RESPONSES_ENDPOINT
output OUTPUT_STORAGE_ACCOUNT string = processor.outputs.OUTPUT_STORAGE_ACCOUNT
output INPUT_QUEUE_NAME string = processor.outputs.INPUT_QUEUE_NAME
output POLICY_MCP_SERVER_URL string = processor.outputs.POLICY_MCP_SERVER_URL
output QUEUE_MCP_SERVER_URL string = processor.outputs.QUEUE_MCP_SERVER_URL
output POLICY_CONNECTOR_NAMESPACE_NAME string = processor.outputs.POLICY_CONNECTOR_NAMESPACE_NAME
output POLICY_CONNECTOR_CONNECTION_NAME string = processor.outputs.POLICY_CONNECTOR_CONNECTION_NAME
output QUEUE_CONNECTOR_CONNECTION_NAME string = processor.outputs.QUEUE_CONNECTOR_CONNECTION_NAME
