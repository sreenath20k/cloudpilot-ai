# IAM for ECS.
#
# Task EXECUTION role: used by the ECS agent (not your code) to pull the image
# from ECR and write logs to CloudWatch. AWS provides a managed policy for exactly this.
#
# No task role is attached: the deployed web service never calls AWS APIs, so by
# least privilege it gets no AWS permissions at all. The chat interface that changes
# the service runs outside the service, with its own credentials.

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${var.project_name}-task-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
