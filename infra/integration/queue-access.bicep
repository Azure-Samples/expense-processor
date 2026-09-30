param expenseStorageAccountName string
param inputQueueName string
param outputQueueNames array
param principalId string

var messageSenderRoleId = 'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a'
var queueContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: expenseStorageAccountName
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}

resource inputQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' existing = {
  parent: queueService
  name: inputQueueName
}

resource inputSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(inputQueue.id, principalId, messageSenderRoleId)
  scope: inputQueue
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', messageSenderRoleId)
  }
}

resource outputQueues 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' existing = [
  for queueName in outputQueueNames: {
    parent: queueService
    name: queueName
  }
]

// Clear Messages requires messages/delete, which the read-only queue role does not grant.
resource outputAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (queueName, i) in outputQueueNames: {
    name: guid(outputQueues[i].id, principalId, queueContributorRoleId)
    scope: outputQueues[i]
    properties: {
      principalId: principalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', queueContributorRoleId)
    }
  }
]
