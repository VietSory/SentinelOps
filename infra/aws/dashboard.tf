# Lane B: an operator view of observed telemetry, not model/SLO acceptance.
# SEARCH retains individual status-code series. No missing-value fill or
# unvalidated EMF-histogram percentile is presented as a healthy measurement.
locals {
  operations_dashboard_name = "${var.name_prefix}-${var.environment}-operations"
  business_routes = [
    { service = "checkout-service", route = "/v1/checkout" },
    { service = "payment-service", route = "/v1/payments/charge" },
    { service = "inventory-service", route = "/v1/inventory/{sku}" },
  ]
  operations_dashboard = {
    start          = "-PT1H"
    periodOverride = "inherit"
    widgets = concat(
      [{
        type = "text", x = 0, y = 0, width = 24, height = 3
        properties = {
          markdown = "# SentinelOps ${var.environment} operations\nObserved business events and control database metrics. Gaps or absent series mean **unknown**, not healthy. Request/error counts are per 60 seconds and split by HTTP status. Model readiness and incident status belong to the protected API/UI. Latency percentiles are withheld until the deployed histogram mapping is verified."
        }
      }],
      flatten([for index, service in local.business_routes : [
        for offset, metric in ["requests", "errors"] : {
          type = "metric", x = index * 8, y = 3 + offset * 6, width = 8, height = 6
          properties = {
            title   = "${service.service} ${metric} / 60s by status"
            region  = var.region
            view    = "timeSeries"
            stacked = false
            period  = 60
            stat    = "Sum"
            metrics = [[{
              id = "e1"
              expression = format(
                "SEARCH('{\"SentinelOps/%s\",\"service\",\"route\",\"status_code\"} MetricName=\"sentinelops_http_%s\" service=\"%s\" route=\"%s\"', 'Sum', 60)",
                var.environment, metric, service.service, service.route
              )
            }]]
          }
        }
      ]]),
      [for index, metric in [
        { name = "CPUUtilization", stat = "Average", unit = "Percent" },
        { name = "DatabaseConnections", stat = "Maximum", unit = "Count" },
        { name = "FreeStorageSpace", stat = "Minimum", unit = "Bytes" },
        ] : {
        type = "metric", x = index * 8, y = 15, width = 8, height = 6
        properties = {
          title   = "Control PostgreSQL ${metric.name} (${metric.unit})"
          region  = var.region
          view    = "timeSeries"
          stacked = false
          period  = 60
          stat    = metric.stat
          # RDS publishes the native unit; keep it in the title rather than
          # adding an unsupported unit rendering option to the metric tuple.
          metrics = [["AWS/RDS", metric.name, "DBInstanceIdentifier", aws_db_instance.control.identifier]]
        }
      }],
      [{
        type = "log", x = 0, y = 21, width = 24, height = 6
        properties = {
          title  = "Recent OTLP business logs — raw envelope"
          region = var.region
          view   = "table"
          query  = "SOURCE '${local.log_group_names.workloads}' | fields @timestamp, @ingestionTime, @logStream, @message | filter @logStream like /^otel-logs-/ | sort @timestamp desc | limit 100"
        }
      }],
    )
  }
}

resource "aws_cloudwatch_dashboard" "operations" {
  dashboard_name = local.operations_dashboard_name
  dashboard_body = jsonencode(local.operations_dashboard)
}
