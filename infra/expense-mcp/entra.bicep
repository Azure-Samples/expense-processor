extension microsoftGraphV1

param appUniqueName string
param appDisplayName string
param functionAppHostname string
param managedIdentityPrincipalId string
param preAuthorizedClientIds array
param serviceManagementReference string = ''

var scopeId = guid(appUniqueName, 'default-scope', 'user_impersonation')
var identifierUri = 'api://${appUniqueName}-${uniqueString(subscription().id, resourceGroup().id, appUniqueName)}'

resource application 'Microsoft.Graph/applications@v1.0' = {
  uniqueName: appUniqueName
  displayName: appDisplayName
  serviceManagementReference: empty(serviceManagementReference) ? null : serviceManagementReference
  signInAudience: 'AzureADMyOrg'
  identifierUris: [identifierUri]
  api: {
    requestedAccessTokenVersion: 2
    oauth2PermissionScopes: [
      {
        id: scopeId
        value: 'user_impersonation'
        isEnabled: true
        type: 'User'
        adminConsentDisplayName: 'Access the expense MCP server'
        adminConsentDescription: 'Submit expense requests and inspect or reset demo decisions.'
        userConsentDisplayName: 'Access the expense MCP server'
        userConsentDescription: 'Submit expense requests and inspect or reset demo decisions.'
      }
    ]
    preAuthorizedApplications: [
      for clientId in preAuthorizedClientIds: {
        appId: clientId
        delegatedPermissionIds: [scopeId]
      }
    ]
  }
  web: {
    redirectUris: ['https://${functionAppHostname}/.auth/login/aad/callback']
    implicitGrantSettings: {
      enableAccessTokenIssuance: false
      enableIdTokenIssuance: true
    }
  }
}

resource servicePrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: application.appId
}

// App Service Authentication uses this credential for sign-in without a client secret.
resource federatedCredential 'Microsoft.Graph/applications/federatedIdentityCredentials@v1.0' = {
  name: '${application.uniqueName}/mcp-function-managed-identity'
  audiences: ['api://AzureADTokenExchange']
  issuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
  subject: managedIdentityPrincipalId
}

output applicationId string = application.appId
output identifierUri string = identifierUri
