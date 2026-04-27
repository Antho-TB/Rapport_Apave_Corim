# Injection des secrets pour l'Application Apave Corim vers Key Vault Centralisé

data "azurerm_key_vault" "central_kv" {
  name                = "kv-tb-ia-agents-secrets"
  resource_group_name = "rg-tb-ia-agents"
}

variable "gemini_project_id" {
  type = string
}

variable "gemini_location" {
  type = string
}

variable "gcp_credentials_json" {
  type = string
  description = "Contenu du fichier gcp_credentials.json"
}

resource "azurerm_key_vault_secret" "gemini_project_id" {
  name         = "GEMINI-PROJECT-ID"
  value        = var.gemini_project_id
  key_vault_id = data.azurerm_key_vault.central_kv.id
}

resource "azurerm_key_vault_secret" "gemini_location" {
  name         = "GEMINI-LOCATION"
  value        = var.gemini_location
  key_vault_id = data.azurerm_key_vault.central_kv.id
}

resource "azurerm_key_vault_secret" "gcp_credentials_json" {
  name         = "GCP-CREDENTIALS-JSON"
  value        = var.gcp_credentials_json
  key_vault_id = data.azurerm_key_vault.central_kv.id
}
