param name string
param resourceToken string
param location string = resourceGroup().location
param tags object = {}
@description('Expense queue endpoint supplied by the processor deployment, accessed with this app identity.')
param expenseQueueServiceUri string

var packageContainerName = 'mcp-app-package'
var identityName = 'mi-expense-mcp-${resourceToken}'

module monitoring '../common/monitoring.bicep' = {
  name: 'expenseMcpMonitoring'
  params: {
    applicationInsightsName: 'appi-expense-mcp-${resourceToken}'
    workspaceName: 'log-expense-mcp-${resourceToken}'
    location: location
    tags: tags
  }
}

module identity 'br/public:avm/res/managed-identity/user-assigned-identity:0.4.1' = {
  name: 'expenseMcpIdentity'
  params: {
    name: identityName
    location: location
    tags: tags
  }
}

module hostStorage 'br/public:avm/res/storage/storage-account:0.8.3' = {
  name: 'expenseMcpHostStorage'
  params: {
    name: 'stmcp${resourceToken}'
    location: location
    tags: tags
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
    blobServices: {
      containers: [
        { name: packageContainerName }
      ]
    }
  }
}

module hostRbac '../common/host-rbac.bicep' = {
  name: 'expenseMcpHostRbac'
  params: {
    storageAccountName: hostStorage.outputs.name
    appInsightsName: monitoring.outputs.name
    managedIdentityPrincipalId: identity.outputs.principalId
  }
}

module plan 'br/public:avm/res/web/serverfarm:0.1.1' = {
  name: 'expenseMcpPlan'
  params: {
    name: 'plan-expense-mcp-${resourceToken}'
    location: location
    tags: tags
    reserved: true
    sku: {
      name: 'FC1'
      tier: 'FlexConsumption'
    }
  }
}

module app '../common/function-app.bicep' = {
  name: 'expenseMcpApp'
  params: {
    name: name
    serviceName: 'mcp'
    location: location
    tags: tags
    applicationInsightsName: monitoring.outputs.name
    appServicePlanId: plan.outputs.resourceId
    runtimeName: 'python'
    runtimeVersion: '3.13'
    storageAccountName: hostStorage.outputs.name
    deploymentStorageContainerName: packageContainerName
    identityId: identity.outputs.resourceId
    identityClientId: identity.outputs.clientId
    appSettings: {
      ExpenseInputStorage__queueServiceUri: expenseQueueServiceUri
      ExpenseInputStorage__credential: 'managedidentity'
      ExpenseInputStorage__clientId: identity.outputs.clientId
      ExpenseStorage__queueServiceUri: expenseQueueServiceUri
      ExpenseStorage__credential: 'managedidentity'
      ExpenseStorage__clientId: identity.outputs.clientId
      AZURE_CLIENT_ID: identity.outputs.clientId
      ENABLE_MULTIPLATFORM_BUILD: 'true'
      PYTHON_ENABLE_INIT_INDEXING: '1'
    }
  }
  dependsOn: [
    hostRbac
  ]
}

output name string = app.outputs.SERVICE_API_NAME
output endpoint string = 'https://${app.outputs.SERVICE_API_HOSTNAME}/runtime/webhooks/mcp'
output principalId string = identity.outputs.principalId
output applicationInsightsResourceId string = monitoring.outputs.resourceId
