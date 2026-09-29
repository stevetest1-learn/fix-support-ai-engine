terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
    random  = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

provider "aws" {
  region = var.aws_region
}

resource "random_id" "suffix" {
  byte_length = 4
}

# ---------- Lambda ----------
locals {
  repository_files = fileset(var.repository_source_dir, "**/*.xml")
}

data "archive_file" "lambda_zip" {
  type        = "zip"
  output_path = "${path.module}/build/lambda_function.zip"

  source {
    content  = file("${path.module}/../lambda/lambda_function.py")
    filename = "lambda_function.py"
  }
  source {
    content  = file("${path.module}/../lambda/crypto_dialects.py")
    filename = "crypto_dialects.py"
  }

  dynamic "source" {
    for_each = local.repository_files
    content {
      content  = file("${var.repository_source_dir}/${source.value}")
      filename = "repository/${source.value}"
    }
  }
}

resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-lambda-role-${random_id.suffix.hex}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "fix_parser" {
  function_name    = "${var.project_name}-parser-${random_id.suffix.hex}"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  timeout          = 15
  memory_size      = 512
}

# ---------- API Gateway (HTTP API) ----------
resource "aws_apigatewayv2_api" "http_api" {
  name          = "${var.project_name}-api-${random_id.suffix.hex}"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type"]
  }
}

resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fix_parser.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "parse_route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /parse"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http_api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fix_parser.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}

# ---------- Frontend: S3 + CloudFront ----------
resource "aws_s3_bucket" "frontend" {
  bucket = "${var.project_name}-frontend-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "frontend_oac" {
  name                              = "${var.project_name}-oac-${random_id.suffix.hex}"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  default_root_object = "index.html"

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "frontend-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend_oac.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods          = ["GET", "HEAD"]
    target_origin_id       = "frontend-s3"
    viewer_protocol_policy = "redirect-to-https"

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  price_class = "PriceClass_100"
}

resource "aws_s3_bucket_policy" "frontend_policy" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontServicePrincipal"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.frontend.arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.frontend.arn }
      }
    }]
  })
}

resource "aws_s3_object" "index_html" {
  bucket = aws_s3_bucket.frontend.id
  key    = "index.html"
  content = templatefile("${path.module}/../frontend/index.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  })
  content_type = "text/html"
  etag = md5(templatefile("${path.module}/../frontend/index.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  }))
}
# ═══════════════════════════════════════════════════════════════════════════════
# FIX LATENCY ANALYSER
# Append this entire block to the bottom of terraform/main.tf
# ═══════════════════════════════════════════════════════════════════════════════

# ── Step 1: pip install matplotlib + numpy into a layer source folder ─────────
# Uses --platform manylinux2014_x86_64 to download Linux-compatible binary
# wheels even when running Terraform on macOS or Windows.

resource "null_resource" "pip_latency_deps" {
  triggers = {
    # Change this string to force a reinstall (e.g. when bumping package versions)
    layer_version = "matplotlib-3-numpy-2-v1"
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      rm -rf ${path.module}/build/latency_layer
      mkdir -p ${path.module}/build/latency_layer/python
      pip3 install matplotlib numpy \
        --platform manylinux2014_x86_64 \
        --only-binary=:all: \
        --python-version 3.12 \
        --implementation cp \
        --target ${path.module}/build/latency_layer/python \
        --upgrade \
        --quiet
    EOT
  }
}

# ── Step 2: zip the installed packages as a Lambda layer ──────────────────────

# Zip the installed packages
data "archive_file" "latency_layer_zip" {
  type        = "zip"
  source_dir  = "${path.module}/build/latency_layer"
  output_path = "${path.module}/build/latency_layer.zip"
  depends_on  = [null_resource.pip_latency_deps]
}

# Separate S3 bucket for Lambda artifacts (no CloudFront policy restriction)
resource "aws_s3_bucket" "lambda_artifacts" {
  bucket = "${var.project_name}-artifacts-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_public_access_block" "lambda_artifacts" {
  bucket                  = aws_s3_bucket.lambda_artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Upload layer zip to S3 — bypasses the 67 MB direct-upload limit
resource "aws_s3_object" "latency_layer_s3" {
  bucket     = aws_s3_bucket.lambda_artifacts.id
  key        = "layers/matplotlib-numpy.zip"
  source     = data.archive_file.latency_layer_zip.output_path
  etag       = data.archive_file.latency_layer_zip.output_base64sha256
  depends_on = [null_resource.pip_latency_deps]
}

# Lambda layer references S3 instead of direct upload
resource "aws_lambda_layer_version" "matplotlib_numpy" {
  layer_name          = "${var.project_name}-matplotlib-numpy-${random_id.suffix.hex}"
  compatible_runtimes = ["python3.12"]
  s3_bucket           = aws_s3_bucket.lambda_artifacts.id
  s3_key              = aws_s3_object.latency_layer_s3.key
  source_code_hash    = data.archive_file.latency_layer_zip.output_base64sha256
  depends_on          = [aws_s3_object.latency_layer_s3]
}

# ── Step 3: zip and deploy the latency Lambda function ────────────────────────

data "archive_file" "latency_lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../lambda_latency/lambda_latency.py"
  output_path = "${path.module}/build/latency_lambda.zip"
  
}


resource "aws_lambda_function" "fix_latency" {
  function_name    = "${var.project_name}-latency-${random_id.suffix.hex}"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_latency.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.latency_lambda_zip.output_path
  source_code_hash = data.archive_file.latency_lambda_zip.output_base64sha256
  # matplotlib chart generation is CPU-intensive; 1024 MB gives headroom
  timeout          = 60
  memory_size      = 1024
  layers           = [aws_lambda_layer_version.matplotlib_numpy.arn]
}

# ── Step 4: API Gateway route  POST /latency ─────────────────────────────────

resource "aws_apigatewayv2_integration" "latency_integration" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fix_latency.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "latency_route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /latency"
  target    = "integrations/${aws_apigatewayv2_integration.latency_integration.id}"
}

resource "aws_lambda_permission" "apigw_invoke_latency" {
  statement_id  = "AllowAPIGatewayInvokeLatency"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fix_latency.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}

# ── Step 5: Upload latency.html to S3 ─────────────────────────────────────────

resource "aws_s3_object" "latency_html" {
  bucket = aws_s3_bucket.frontend.id
  key    = "latency.html"
  content = templatefile("${path.module}/../frontend/latency.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  })
  content_type = "text/html"
  etag = md5(templatefile("${path.module}/../frontend/latency.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  }))
}

# ═══════════════════════════════════════════════════════════════════════════════
# FIX TRADE LATENCY ANALYSER
# Append this block to the bottom of terraform/main.tf
# Reuses: aws_iam_role.lambda_role, aws_lambda_layer_version.matplotlib_numpy,
#         aws_apigatewayv2_api.http_api, aws_apigatewayv2_stage.default,
#         aws_s3_bucket.frontend, aws_cloudfront_distribution.frontend
# ═══════════════════════════════════════════════════════════════════════════════

# ── Lambda function ────────────────────────────────────────────────────────────

data "archive_file" "trade_latency_lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../lambda_trade_latency/lambda_trade_latency.py"
  output_path = "${path.module}/build/trade_latency_lambda.zip"
}

resource "aws_lambda_function" "fix_trade_latency" {
  function_name    = "${var.project_name}-trade-latency-${random_id.suffix.hex}"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_trade_latency.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.trade_latency_lambda_zip.output_path
  source_code_hash = data.archive_file.trade_latency_lambda_zip.output_base64sha256
  timeout          = 60
  memory_size      = 1024

  # Reuse the existing matplotlib + numpy layer
  layers = [aws_lambda_layer_version.matplotlib_numpy.arn]
}

# ── API Gateway  POST /trade-latency ──────────────────────────────────────────

resource "aws_apigatewayv2_integration" "trade_latency_integration" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fix_trade_latency.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "trade_latency_route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /trade-latency"
  target    = "integrations/${aws_apigatewayv2_integration.trade_latency_integration.id}"
}

resource "aws_lambda_permission" "apigw_invoke_trade_latency" {
  statement_id  = "AllowAPIGatewayInvokeTradeLatency"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fix_trade_latency.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}

# ── Frontend page ──────────────────────────────────────────────────────────────

resource "aws_s3_object" "trade_latency_html" {
  bucket = aws_s3_bucket.frontend.id
  key    = "trade-latency.html"
  content = templatefile("${path.module}/../frontend/trade_latency.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  })
  content_type = "text/html"
  etag = md5(templatefile("${path.module}/../frontend/trade_latency.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  }))
}
# ═══════════════════════════════════════════════════════════════════════════════
# LOG SEARCH ENGINE
# Append this entire block to the bottom of terraform/main.tf
# No Lambda layer needed — pure Python stdlib, no external packages.
# Reuses: aws_iam_role.lambda_role, aws_apigatewayv2_api.http_api,
#         aws_apigatewayv2_stage.default, aws_s3_bucket.frontend
# ═══════════════════════════════════════════════════════════════════════════════

# ── Lambda function ────────────────────────────────────────────────────────────

data "archive_file" "log_search_lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../lambda_log_search/lambda_log_search.py"
  output_path = "${path.module}/build/log_search_lambda.zip"
}

resource "aws_lambda_function" "log_search" {
  function_name    = "${var.project_name}-log-search-${random_id.suffix.hex}"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_log_search.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.log_search_lambda_zip.output_path
  source_code_hash = data.archive_file.log_search_lambda_zip.output_base64sha256
  timeout          = 30
  memory_size      = 512
  # No layers needed — pure Python stdlib
}

# ── API Gateway  POST /log-search ─────────────────────────────────────────────

resource "aws_apigatewayv2_integration" "log_search_integration" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.log_search.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "log_search_route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /log-search"
  target    = "integrations/${aws_apigatewayv2_integration.log_search_integration.id}"
}

resource "aws_lambda_permission" "apigw_invoke_log_search" {
  statement_id  = "AllowAPIGatewayInvokeLogSearch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.log_search.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}

# ── Frontend page ──────────────────────────────────────────────────────────────

resource "aws_s3_object" "log_search_html" {
  bucket = aws_s3_bucket.frontend.id
  key    = "log-search.html"
  content = templatefile("${path.module}/../frontend/log_search.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  })
  content_type = "text/html"
  etag = md5(templatefile("${path.module}/../frontend/log_search.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  }))
}

# ═══════════════════════════════════════════════════════════════════════════
# STEP 2 — APPEND this entire block to the bottom of terraform/main.tf
# ═══════════════════════════════════════════════════════════════════════════

# ── Lambda function ─────────────────────────────────────────────────────────

data "archive_file" "market_data_lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../lambda_market_data/lambda_market_data.py"
  output_path = "${path.module}/build/market_data_lambda.zip"
}

resource "aws_lambda_function" "market_data" {
  function_name    = "${var.project_name}-market-data-${random_id.suffix.hex}"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_market_data.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.market_data_lambda_zip.output_path
  source_code_hash = data.archive_file.market_data_lambda_zip.output_base64sha256
  # Outbound call to Coinbase API — 10s timeout + overhead
  timeout          = 15
  memory_size      = 256
  # No layer needed — pure Python stdlib (urllib)
}

# ── API Gateway  GET /market-data ───────────────────────────────────────────

resource "aws_apigatewayv2_integration" "market_data_integration" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.market_data.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "market_data_route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "GET /market-data"
  target    = "integrations/${aws_apigatewayv2_integration.market_data_integration.id}"
}

resource "aws_lambda_permission" "apigw_invoke_market_data" {
  statement_id  = "AllowAPIGatewayInvokeMarketData"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.market_data.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}

# ── Frontend page ────────────────────────────────────────────────────────────

resource "aws_s3_object" "market_data_html" {
  bucket = aws_s3_bucket.frontend.id
  key    = "market-data.html"
  content = templatefile("${path.module}/../frontend/market_data.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  })
  content_type = "text/html"
  etag = md5(templatefile("${path.module}/../frontend/market_data.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  }))
}
# ═══════════════════════════════════════════════════════════════════════════════
# STP GENERATOR — append to bottom of terraform/main.tf
# ═══════════════════════════════════════════════════════════════════════════════

data "archive_file" "stp_lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../lambda_stp/lambda_stp.py"
  output_path = "${path.module}/build/stp_lambda.zip"
}

resource "aws_lambda_function" "stp_generator" {
  function_name    = "${var.project_name}-stp-${random_id.suffix.hex}"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_stp.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.stp_lambda_zip.output_path
  source_code_hash = data.archive_file.stp_lambda_zip.output_base64sha256
  timeout          = 15
  memory_size      = 256
}

resource "aws_apigatewayv2_integration" "stp_integration" {
  api_id                 = aws_apigatewayv2_api.http_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.stp_generator.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "stp_route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /stp-generate"
  target    = "integrations/${aws_apigatewayv2_integration.stp_integration.id}"
}

resource "aws_lambda_permission" "apigw_invoke_stp" {
  statement_id  = "AllowAPIGatewayInvokeSTP"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.stp_generator.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}

resource "aws_s3_object" "stp_html" {
  bucket = aws_s3_bucket.frontend.id
  key    = "stp.html"
  content = templatefile("${path.module}/../frontend/stp.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  })
  content_type = "text/html"
  etag = md5(templatefile("${path.module}/../frontend/stp.html", {
    api_endpoint = trimsuffix(aws_apigatewayv2_stage.default.invoke_url, "/")
  }))
}