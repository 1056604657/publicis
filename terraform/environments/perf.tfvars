# perf 环境变量（性能压测，中等规格，接近生产负载）
environment              = "perf"
region                   = "cn-shanghai"
rds_instance_class       = "mysql.n2.medium.1"
redis_instance_type      = "redis.master.stand.default"
ack_worker_instance_type = "ecs.g6.xlarge"
ack_worker_count         = 3
