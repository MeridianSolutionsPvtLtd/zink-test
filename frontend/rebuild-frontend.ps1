# Rebuild and redeploy frontend only

$ACR = "meridianai"
$NAMESPACE = "ai-team"

Write-Host "Rebuilding frontend..." -ForegroundColor Yellow
az acr build --registry $ACR --image "stockpulse-frontend:v2" --file Dockerfile.frontend .

if ($LASTEXITCODE -eq 0) {
    Write-Host "Frontend built successfully" -ForegroundColor Green
    Write-Host ""
    Write-Host "Updating deployment..." -ForegroundColor Yellow
    kubectl set image deployment/stockpulse stockpulse=$ACR.azurecr.io/stockpulse-frontend:v2 -n $NAMESPACE
    kubectl rollout status deployment/stockpulse -n $NAMESPACE --timeout=120s
    Write-Host ""
    Write-Host "Frontend updated!" -ForegroundColor Green
    Write-Host "Access: http://20.204.230.107" -ForegroundColor Cyan
} else {
    Write-Host "Build failed" -ForegroundColor Red
}
