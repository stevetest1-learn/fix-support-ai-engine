output "frontend_url" {
  description = "Public HTTPS URL for the FIX parser website"
  value       = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}

output "api_endpoint" {
  description = "API Gateway endpoint used by the frontend"
  value       = "${trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")}/parse"
}