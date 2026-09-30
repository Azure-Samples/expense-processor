// Creates the input and per-outcome output queues used by the expense processor.
// The input queue (expense-requests) MUST exist for the queue trigger to bind. The three
// output queues (expense-approved / expense-review / expense-flagged) are pre-created here so
// the routing tool always has a valid destination for every decision.
param storageAccountName string

@description('Queue names to create in the storage account (input + per-outcome output queues).')
param queueNames array

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource queueService 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource queues 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = [
  for queueName in queueNames: {
    parent: queueService
    name: queueName
  }
]
