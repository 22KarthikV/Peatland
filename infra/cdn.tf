# SPA hosting (I2): private S3 web bucket behind CloudFront with Origin Access Control.
# AWS-only, validate/plan only; gated on var.enable_aws_only. The bucket has no public access
# and no website endpoint: only this distribution may read it.
#
# Deploy: `npm --prefix web run build`, `aws s3 sync web/dist s3://<web_bucket>`, then invalidate
# /index.html. Not automated here.

variable "cloudfront_price_class" {
  description = "PriceClass_100 serves North America and Europe only, the cheapest."
  type        = string
  default     = "PriceClass_100"
}

locals {
  web_bucket_name = "peatwatch-web${var.name_suffix}"
  # Origin the browser sends to the API; also the API's CORS allow-list (CORS_ORIGINS).
  web_origin = var.enable_aws_only ? "https://${aws_cloudfront_distribution.web[0].domain_name}" : ""

  # AWS managed policy IDs (constants, so no lookup call is needed at plan time).
  cache_policy_caching_optimized = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  response_policy_security       = "67f7725c-6f97-4210-82d7-5512b4e5f1de" # SecurityHeadersPolicy
}

resource "aws_s3_bucket" "web" {
  count         = local.aws_only
  bucket        = local.web_bucket_name
  force_destroy = var.environment != "prod"
}

resource "aws_s3_bucket_public_access_block" "web" {
  count                   = local.aws_only
  bucket                  = aws_s3_bucket.web[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  count  = local.aws_only
  bucket = aws_s3_bucket.web[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudfront_origin_access_control" "web" {
  count                             = local.aws_only
  name                              = "peatwatch-web${var.name_suffix}"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "web" {
  count               = local.aws_only
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = var.cloudfront_price_class
  comment             = "PeatWatch SPA${var.name_suffix}"

  origin {
    origin_id                = "web-bucket"
    domain_name              = aws_s3_bucket.web[0].bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.web[0].id
  }

  default_cache_behavior {
    target_origin_id           = "web-bucket"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = local.cache_policy_caching_optimized
    response_headers_policy_id = local.response_policy_security
  }

  # Client-side routing: a private bucket answers 403 for unknown keys, so serve the SPA shell.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Default *.cloudfront.net certificate (TLS 1.2 minimum needs a custom domain + ACM cert in
  # us-east-1; out of scope).
  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

resource "aws_s3_bucket_policy" "web" {
  count  = local.aws_only
  bucket = aws_s3_bucket.web[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.web[0].arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.web[0].arn }
      }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.web]
}
