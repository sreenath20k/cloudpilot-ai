# Amazon ECR: the private registry that stores the Docker image.

resource "aws_ecr_repository" "app" {
  name                 = var.project_name
  image_tag_mutability = "MUTABLE" # lets us re-push the "latest" tag

  # Lets `terraform destroy` delete the repository even if it still holds images.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true # free basic vulnerability scan on every push
  }
}

# Keep only the 5 newest images to keep storage costs near zero.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}
