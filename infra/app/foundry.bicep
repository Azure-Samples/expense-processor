param accountName string
param projectName string
param location string = resourceGroup().location
param tags object = {}
param modelDeploymentName string = 'gpt-5.4'
param modelName string = 'gpt-5.4'
param modelVersion string = '2026-03-05'
param deploymentCapacity int = 50

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  tags: tags
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    publicNetworkAccess: 'Enabled'
    // Local (API-key) auth is disabled to satisfy the enforced org policy
    // "disable local auth for Cognitive Services" (Safe Secrets Standard).
    // API Management authenticates to Foundry with its system-assigned managed identity.
    // The Function identity has no direct model role, so deployed calls cannot bypass the gateway.
    disableLocalAuth: true
  }
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-10-01-preview' = {
  parent: foundryAccount
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: projectName
    description: 'Expense processor hosted skill sample project'
  }
}

resource foundryModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: foundryAccount
  name: modelDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: deploymentCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
}

output accountName string = foundryAccount.name
output projectName string = foundryProject.name
output projectEndpoint string = '${foundryAccount.properties.endpoints['AI Foundry API']}api/projects/${foundryProject.name}'
output openAiEndpoint string = foundryAccount.properties.endpoints['OpenAI Language Model Instance API']
output contentSafetyEndpoint string = foundryAccount.properties.endpoints['Content Safety']
output modelDeploymentName string = foundryModelDeployment.name
