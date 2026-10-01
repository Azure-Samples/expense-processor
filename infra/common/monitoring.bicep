param location string = resourceGroup().location
param tags object = {}
param applicationInsightsName string
param workspaceName string

module workspace 'br/public:avm/res/operational-insights/workspace:0.7.0' = {
  name: 'logAnalytics'
  params: {
    name: workspaceName
    location: location
    tags: tags
    dataRetention: 30
  }
}

module applicationInsights 'br/public:avm/res/insights/component:0.4.1' = {
  name: 'applicationInsights'
  params: {
    name: applicationInsightsName
    location: location
    tags: tags
    workspaceResourceId: workspace.outputs.resourceId
    disableLocalAuth: true
  }
}

output name string = applicationInsights.outputs.name
output resourceId string = applicationInsights.outputs.resourceId
output workspaceResourceId string = workspace.outputs.resourceId
