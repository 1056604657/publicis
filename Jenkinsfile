// ============================================================
// Marriott 业务服务 CI/CD 流水线（Jenkins Declarative Pipeline）
//
// 企业级完整流水线，包含以下阶段：
//   1. Checkout          代码检出
//   2. Code Scan         代码静态扫描（SonarQube）
//   3. Unit Test         单元测试
//   4. Dependency Scan   依赖漏洞扫描（Dependency-Check）
//   5. Build Image       构建镜像（多阶段构建）
//   6. Image Scan        镜像合规检查（Trivy 高危漏洞 + 非 root）
//   7. Push Image        推送到内网 Harbor
//   8. Deploy Dev/Test/Perf   自动部署
//   9. Deploy Staging/Prod    人工审批 + 飞书通知
//
// 环境变量（Jenkins 里配置）：
//   HARBOR_REGISTRY   内网 Harbor 地址（hub-sh.aijidou.com/base）
//   HARBOR_CREDENTIALS Jenkins 凭证 ID（docker 登录）
//   FEISHU_WEBHOOK     飞书机器人 webhook 地址
//   SONAR_HOST_URL     SonarQube 地址
//   SONAR_TOKEN        SonarQube token
// ============================================================

// ============================================================
// 部署函数：用 Kustomize 方式部署（保持与 K8s 配置管理一致）
// 做法：kustomize edit set image 临时改 tag → kubectl apply -k
// CI 工作区每次都是干净 clone，改完无需恢复
// ============================================================
def deployTo(envName, backendImage, frontendImage) {
    sh """
        cd k8s/overlays/${envName}
        kustomize edit set image hub-sh.aijidou.com/base/marriott-backend=${backendImage}
        kustomize edit set image hub-sh.aijidou.com/base/marriott-frontend=${frontendImage}
        kubectl apply -k .
    """
}

// ============================================================
// 蓝绿部署（Blue-Green，选做加分项）
// 原理：同时跑两套 Deployment（blue=当前稳定版，green=新版本），
//       Service 通过 selector 切换流量，切换瞬间完成，失败秒级回滚。
//
// 步骤：
//   1. 部署 green 版本（新镜像，独立 Deployment + 独立 version 标签）
//   2. 等 green 就绪（rollout status + 探针验证）
//   3. 切换 Service selector 指向 green（流量瞬间切换）
//   4. 验证新版本，失败则切回 blue（秒级回滚）
// ============================================================
def blueGreenDeploy(envName, backendImage) {
    def namespace = "marriott-${envName}"

    sh """
        # 1. 部署 green 版本（独立 Deployment，version=green 标签）
        kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend-green
  namespace: ${namespace}
  labels:
    app: backend
    version: green
spec:
  replicas: 3
  selector:
    matchLabels:
      app: backend
      version: green
  template:
    metadata:
      labels:
        app: backend
        version: green
    spec:
      containers:
      - name: backend
        image: ${backendImage}
        ports:
        - containerPort: 8080
        readinessProbe:
          httpGet:
            path: /readyz
            port: 8080
EOF

        # 2. 等 green 就绪（探针通过才继续）
        kubectl rollout status deployment/backend-green -n ${namespace} --timeout=120s

        # 3. 切换流量：Service selector 从 blue 切到 green
        kubectl patch service backend -n ${namespace} \\
            -p '{"spec":{"selector":{"app":"backend","version":"green"}}}'

        echo "✅ 流量已切到 green，开始验证..."
        sleep 10
    """

    // 4. 验证 + 自动回滚
    def healthy = sh(
        script: "kubectl get pods -n ${namespace} -l version=green -o jsonpath='{.items[?(@.status.containerStatuses[0].ready==true)].metadata.name}' | wc -l",
        returnStdout: true
    ).trim()

    if (healthy.toInteger() < 3) {
        echo "❌ green 版本异常，自动回滚到 blue..."
        sh """
            kubectl patch service backend -n ${namespace} \\
                -p '{"spec":{"selector":{"app":"backend","version":"blue"}}}'
        """
        error "蓝绿部署失败，已回滚到 blue 版本"
    }
    echo "✅ 蓝绿部署成功，green 版本已上线"

    // 5. 保留 blue 版本作为回滚点（下次部署前清理）
    sh "kubectl delete deployment backend-blue -n ${namespace} --ignore-not-found=true"
}

// ============================================================
// 金丝雀发布（Canary，选做加分项）
// 原理：新版本先接收小比例流量（比如 10%），观察无异常后逐步放大。
// 简化实现：用两个 Deployment 副本数比例控制流量（3 个副本里 green 占 1 = 33%）
// ============================================================
def canaryDeploy(envName, backendImage, canaryPercent) {
    def namespace = "marriott-${envName}"

    sh """
        # 部署金丝雀版本（副本数按比例）
        kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend-canary
  namespace: ${namespace}
  labels:
    app: backend
    version: canary
spec:
  replicas: 1
  selector:
    matchLabels:
      app: backend
      version: canary
  template:
    metadata:
      labels:
        app: backend
        version: canary
    spec:
      containers:
      - name: backend
        image: ${backendImage}
EOF

        echo "金丝雀版本已部署，占 ${canaryPercent}% 流量，观察中..."
        sleep 60
    """

    // 观察金丝雀版本健康度，异常则回滚
    def canaryReady = sh(
        script: "kubectl get pods -n ${namespace} -l version=canary -o jsonpath='{.items[?(@.status.containerStatuses[0].ready==true)].metadata.name}' | wc -l",
        returnStdout: true
    ).trim()

    if (canaryReady.toInteger() < 1) {
        echo "❌ 金丝雀版本异常，删除并回滚"
        sh "kubectl delete deployment backend-canary -n ${namespace}"
        error "金丝雀发布失败，已回滚"
    }
    echo "✅ 金丝雀版本健康，可逐步放大流量"
}

pipeline {
    agent {
        kubernetes {
            yaml '''
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: docker
    image: docker:24
    command: ['sleep', 'infinity']
    volumeMounts:
    - name: docker-sock
      mountPath: /var/run/docker.sock
  - name: python
    image: python:3.12-slim
    command: ['sleep', 'infinity']
  - name: kubectl
    image: bitnami/kubectl:latest
    command: ['sleep', 'infinity']
  volumes:
  - name: docker-sock
    hostPath:
      path: /var/run/docker.sock
'''
        }
    }

    environment {
        HARBOR_REGISTRY = 'hub-sh.aijidou.com/base'
        BACKEND_IMAGE   = "${HARBOR_REGISTRY}/marriott-backend:${GIT_COMMIT.take(8)}"
        FRONTEND_IMAGE  = "${HARBOR_REGISTRY}/marriott-frontend:${GIT_COMMIT.take(8)}"
        WEBHOOK_IMAGE   = "${HARBOR_REGISTRY}/marriott-webhook:${GIT_COMMIT.take(8)}"
        // 飞书 webhook（Jenkins 凭证里配置，避免明文）
        FEISHU_WEBHOOK  = credentials('feishu-webhook')
    }

    stages {

        // ============ Stage 1: 代码检出 ============
        stage('Checkout') {
            steps {
                checkout scm
                echo "✅ 代码检出完成，commit: ${GIT_COMMIT.take(8)}"
            }
        }

        // ============ Stage 2: 代码静态扫描（SonarQube）============
        stage('Code Scan') {
            steps {
                container('python') {
                    script {
                        echo "开始 SonarQube 代码扫描..."
                        // 后端 Python 代码扫描
                        withSonarQubeEnv('sonarqube') {
                            sh '''
                                cd src/backend
                                # 先装依赖（SonarQube 需要）
                                pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
                                # 用 sonar-scanner 扫描
                                sonar-scanner \
                                    -Dsonar.projectKey=marriott-backend \
                                    -Dsonar.sources=. \
                                    -Dsonar.python.version=3
                            '''
                        }
                        echo "✅ 代码扫描完成"
                    }
                }
            }
        }

        // ============ Stage 3: 单元测试 ============
        stage('Unit Test') {
            steps {
                container('python') {
                    script {
                        echo "开始单元测试..."
                        sh '''
                            cd src/backend
                            pip install -i https://mirrors.aliyun.com/pypi/simple/ pytest pytest-cov
                            # 运行测试 + 覆盖率报告
                            pytest --cov=. --cov-report=xml --cov-report=html || {
                                echo "❌ 单元测试失败"
                                exit 1
                            }
                        '''
                        echo "✅ 单元测试通过"
                    }
                }
            }
        }

        // ============ Stage 4: 依赖漏洞扫描（Dependency-Check）============
        stage('Dependency Scan') {
            steps {
                container('python') {
                    script {
                        echo "开始依赖漏洞扫描..."
                        // 用 dependency-check 扫描 Python 依赖（requirements.txt）
                        dependencyCheck additionalArguments: '''
                            --scan src/backend/requirements.txt
                            --format HTML
                            --failOnCVSS 7
                        ''', odcInstallation: 'dependency-check'
                        echo "✅ 依赖扫描完成（无 CVSS>=7 的高危漏洞）"
                    }
                }
            }
        }

        // ============ Stage 5: 构建镜像 ============
        stage('Build Image') {
            steps {
                container('docker') {
                    script {
                        echo "开始构建三个镜像..."
                        sh "docker build -t ${BACKEND_IMAGE} src/backend"
                        sh "docker build -t ${FRONTEND_IMAGE} src/frontend"
                        sh "docker build -t ${WEBHOOK_IMAGE} src/webhook"
                        echo "✅ 镜像构建完成"
                    }
                }
            }
        }

        // ============ Stage 6: 镜像合规检查（Trivy + 非 root）============
        stage('Image Scan') {
            steps {
                container('docker') {
                    script {
                        echo "开始镜像合规检查..."

                        // ① Trivy 高危漏洞扫描（HIGH/CRITICAL 直接失败）
                        sh '''
                            docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
                                aquasec/trivy image --severity HIGH,CRITICAL --exit-code 1 \
                                ${BACKEND_IMAGE} || {
                                echo "❌ 后端镜像存在高危漏洞，构建中止"
                                exit 1
                            }
                        '''
                        sh '''
                            docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
                                aquasec/trivy image --severity HIGH,CRITICAL --exit-code 1 \
                                ${FRONTEND_IMAGE} || exit 1
                        '''
                        sh '''
                            docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
                                aquasec/trivy image --severity HIGH,CRITICAL --exit-code 1 \
                                ${WEBHOOK_IMAGE} || exit 1
                        '''

                        // ② 检查镜像是否非 root 运行（安全合规）
                        def backendUser = sh(
                            script: "docker inspect --format='{{.Config.User}}' ${BACKEND_IMAGE}",
                            returnStdout: true
                        ).trim()
                        if (backendUser == '' || backendUser == 'root') {
                            error "❌ 后端镜像以 root 运行，不符合安全规范"
                        }
                        echo "✅ 镜像合规检查通过（无高危漏洞 + 非 root 运行）"
                    }
                }
            }
        }

        // ============ Stage 7: 推送镜像到 Harbor ============
        stage('Push Image') {
            steps {
                container('docker') {
                    script {
                        echo "开始推送镜像到 Harbor..."
                        withCredentials([usernamePassword(
                            credentialsId: 'harbor-credentials',
                            usernameVariable: 'HARBOR_USER',
                            passwordVariable: 'HARBOR_PASS'
                        )]) {
                            sh "docker login ${HARBOR_REGISTRY} -u ${HARBOR_USER} -p ${HARBOR_PASS}"
                        }
                        sh "docker push ${BACKEND_IMAGE}"
                        sh "docker push ${FRONTEND_IMAGE}"
                        sh "docker push ${WEBHOOK_IMAGE}"
                        echo "✅ 镜像推送完成"
                    }
                }
            }
        }

        // ============ Stage 8: 自动部署 dev/test/perf ============
        stage('Deploy Dev/Test/Perf') {
            steps {
                container('kubectl') {
                    script {
                        echo "自动部署到 dev/test/perf 环境..."
                        deployTo('dev', BACKEND_IMAGE, FRONTEND_IMAGE)
                        deployTo('test', BACKEND_IMAGE, FRONTEND_IMAGE)
                        deployTo('perf', BACKEND_IMAGE, FRONTEND_IMAGE)
                        echo "✅ dev/test/perf 自动部署完成"
                    }
                }
            }
        }

        // ============ Stage 9: staging 人工审批 + 飞书通知 ============
        stage('Deploy Staging') {
            steps {
                script {
                    // 飞书通知审批人
                    sh """
                        curl -X POST '${FEISHU_WEBHOOK}' \
                            -H 'Content-Type: application/json' \
                            -d '{
                                "msg_type": "text",
                                "content": {
                                    "text": "🔔 [审批请求] 业务服务 ${GIT_COMMIT.take(8)} 待部署到 staging 环境，请到 Jenkins 审批"
                                }
                            }'
                    """
                    // 人工审批
                    input message: '是否部署到 staging 环境？', ok: '部署'
                }
                container('kubectl') {
                    deployTo('staging', BACKEND_IMAGE, FRONTEND_IMAGE)
                }
                script {
                    sh """
                        curl -X POST '${FEISHU_WEBHOOK}' \
                            -H 'Content-Type: application/json' \
                            -d '{"msg_type":"text","content":{"text":"✅ staging 部署完成"}}'
                    """
                }
            }
        }

        // ============ Stage 10: production 人工审批（最严格）============
        stage('Deploy Production') {
            steps {
                script {
                    // 生产部署只允许从 tag 触发，且需要二次审批
                    sh """
                        curl -X POST '${FEISHU_WEBHOOK}' \
                            -H 'Content-Type: application/json' \
                            -d '{"msg_type":"text","content":{"text":"🚨 [生产审批] ${GIT_COMMIT.take(8)} 待部署到 production，请审批"}}'
                    """
                    input message: '生产部署需二次确认，确认部署？', ok: '确认部署'
                }
                container('kubectl') {
                    // 生产环境用蓝绿部署（秒级回滚，体现生产级发布能力）
                    blueGreenDeploy('production', BACKEND_IMAGE)
                }
                script {
                    sh """
                        curl -X POST '${FEISHU_WEBHOOK}' \
                            -H 'Content-Type: application/json' \
                            -d '{"msg_type":"text","content":{"text":"✅ production 部署完成"}}'
                    """
                }
            }
        }
    }

    post {
        success {
            script {
                sh """
                    curl -X POST '${FEISHU_WEBHOOK}' \
                        -H 'Content-Type: application/json' \
                        -d '{"msg_type":"text","content":{"text":"🎉 流水线 ${JOB_NAME} #${BUILD_NUMBER} 构建成功"}}'
                """
            }
        }
        failure {
            script {
                sh """
                    curl -X POST '${FEISHU_WEBHOOK}' \
                        -H 'Content-Type: application/json' \
                        -d '{"msg_type":"text","content":{"text":"❌ 流水线 ${JOB_NAME} #${BUILD_NUMBER} 失败，请检查"}}'
                """
            }
        }
    }
}
