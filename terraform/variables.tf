variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name prefix for resource naming"
  type        = string
  default     = "fix-parser"
}

variable "repository_source_dir" {
  description = "Absolute local path to the FIX Repository directory (the folder containing FIX.4.0, FIX.4.2, etc. subfolders) that gets bundled into the Lambda package"
  type        = string
}