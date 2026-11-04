#!/usr/bin/env bash
# Fix zinklondon-backend-app ACRTokenRetrievalFailure / ImagePullFailure
# Run in Azure Portal → Cloud Shell (Bash), after: az login
set -euo pipefail

WEBAPP="${WEBAPP:-zinklondon-backend-app}"
ACR="${ACR:-zinklondon}"
IMAGE_TAG="${IMAGE_TAG:-latest}"   # or full SHA tag from GitHub Actions
CONTAINER="${CONTAINER:-main}"

if [ -z "${RG:-}" ]; then
  RG=$(az webapp show --name "$WEBAPP" --query resourceGroup -o tsv)
fi

ACR_LOWER=$(echo "$ACR" | tr '[:upper:]' '[:lower:]')
IMAGE="${ACR_LOWER}.azurecr.io/${CONTAINER}:${IMAGE_TAG}"
REGISTRY_URL="https://${ACR_LOWER}.azurecr.io"
APP_ID=$(az webapp show --name "$WEBAPP" --resource-group "$RG" --query id -o tsv)

echo "Web App: $WEBAPP (RG=$RG)"
echo "Image:   $IMAGE"

az webapp identity assign --name "$WEBAPP" --resource-group "$RG" >/dev/null
PRINCIPAL_ID=$(az webapp identity show --name "$WEBAPP" --resource-group "$RG" --query principalId -o tsv)
ACR_ID=$(az acr show --name "$ACR" --query id -o tsv)

if ! az role assignment list --assignee-object-id "$PRINCIPAL_ID" --scope "$ACR_ID" --role AcrPull --query "[0].id" -o tsv | grep -q .; then
  az role assignment create \
    --assignee-object-id "$PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role AcrPull \
    --scope "$ACR_ID"
  echo "AcrPull assigned; waiting 45s for propagation..."
  sleep 45
fi

# Prefer managed identity when AcrPull exists
az resource update --ids "$APP_ID" --api-version "2024-11-01" --set properties.siteConfig.acrUseManagedIdentityCreds=true || true
az webapp config container set \
  --name "$WEBAPP" \
  --resource-group "$RG" \
  --docker-custom-image-name "$IMAGE" \
  --docker-registry-server-url "$REGISTRY_URL"

if ! az webapp restart --name "$WEBAPP" --resource-group "$RG"; then
  echo "Restart issued"
fi

echo "Done. If pull still fails, run with admin creds:"
echo "  az acr update --name $ACR --admin-enabled true"
echo "  az resource update --ids $APP_ID --api-version 2024-11-01 --set properties.siteConfig.acrUseManagedIdentityCreds=false"
echo "  az webapp config container set ... --docker-registry-server-user ... --docker-registry-server-password ..."
