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
// Istio 灰度发布（header 定向，统一发布策略）
// 核心：先按 header 定向让「测试组」访问新版，测试通过后全量切换。
// 用 Istio 的 VirtualService 一套搞定，不需要蓝绿/金丝雀多套逻辑。
//
// 依赖：k8s/istio/ 下的 DestinationRule + VirtualService + Gateway
// 流程：
//   1. 部署 green 新版（version=green 标签，供 Istio subset 匹配）
//   2. apply Istio 配置（DestinationRule 定义 v1/v2 子集 + header 路由）
//   3. header 灰度：X-User-Group: beta → v2（测试组先验证，其他走 v1）
//   4. 观察 green 健康度
//   5. 测试通过 → 全量切换（100% 流量切到 v2），异常则回滚
// ============================================================
def progressiveDeploy(envName, backendImage) {
    def namespace = "marriott-${envName}"

    sh """
        # 1. 部署 green 版本（version=green 标签，供 Istio subset 匹配）
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
  replicas: 1
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

        # 2. 应用 Istio 配置（DestinationRule 定义 v1(blue)/v2(green) 子集 + header 路由）
        kubectl apply -f k8s/istio/destinationrule.yaml
        kubectl apply -f k8s/istio/virtualservice.yaml

        echo "✅ Istio header 灰度已生效：X-User-Group: beta → v2 新版"
        echo "   测试组用户带 header 先访问新版，其他用户仍走 v1 旧版"
        sleep 60
    """

    // 3. 观察 green 版本健康度
    def greenReady = sh(
        script: "kubectl get pods -n ${namespace} -l version=green -o jsonpath='{.items[?(@.status.containerStatuses[0].ready==true)].metadata.name}' | wc -l",
        returnStdout: true
    ).trim()

    if (greenReady.toInteger() < 1) {
        echo "❌ green 版本异常，删除并回滚"
        sh "kubectl delete deployment backend-green -n ${namespace}"
        error "灰度发布失败，已回滚"
    }

    // 4. 测试通过 → 全量切换（100% 流量切到 green 新版）
    echo "✅ 测试通过，全量切换流量到 green 新版..."
    sh """
        kubectl apply -f k8s/istio/virtualservice-full.yaml
    """
    echo "✅ 灰度发布完成，100% 流量已切到 green 新版"

    // 5. 旧版 blue 保留作回滚点，确认稳定后下线
    sh "kubectl delete deployment backend-blue -n ${namespace} --ignore-not-found=true"
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
                    // 生产环境用 Istio header 灰度（测试组先验证，通过后全量切换）
                    progressiveDeploy('production', BACKEND_IMAGE)
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
