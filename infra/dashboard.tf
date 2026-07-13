# ============================================================================
# Dashboard del dueño: S3 privado + CloudFront (OAC).
# Deploy: make dashboard-build && aws s3 sync apps/dashboard/dist s3://... &&
#         aws cloudfront create-invalidation (ver infra/README.md).
# ============================================================================

resource "aws_s3_bucket" "dashboard" {
  bucket = "aiveridia-events-dashboard-${var.env}"
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  bucket                  = aws_s3_bucket.dashboard.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "dashboard" {
  name                              = "aiveridia-dashboard-${var.env}"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "dashboard" {
  enabled             = true
  comment             = "aiVeridia Events — dashboard del dueño (${var.env})"
  default_root_object = "index.html"
  price_class         = "PriceClass_100" # US/EU (cubre acceso desde Perú)

  origin {
    domain_name              = aws_s3_bucket.dashboard.bucket_regional_domain_name
    origin_id                = "s3-dashboard"
    origin_access_control_id = aws_cloudfront_origin_access_control.dashboard.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-dashboard"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
  }

  # SPA: cualquier ruta del router devuelve index.html
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true # dominio propio + ACM en fase de marca
  }
}

resource "aws_s3_bucket_policy" "dashboard_cloudfront" {
  bucket = aws_s3_bucket.dashboard.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.dashboard.arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.dashboard.arn }
      }
    }]
  })
}

output "dashboard_url" {
  value = "https://${aws_cloudfront_distribution.dashboard.domain_name}"
}

output "dashboard_bucket" {
  value = aws_s3_bucket.dashboard.bucket
}
