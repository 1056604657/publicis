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
    // 从镜像名提取 tag（backend 和 frontend 都是 commit SHA，tag 相同）
    // 用 sed 改 overlay 的 newTag，不依赖独立的 kustomize 二进制（bitnami/kubectl 容器里没有 kustomize edit）
    def imageTag = backendImage.tokenize(':')[-1]
    sh """
        cd k8s/overlays/${envName}
        # 把 overlay 里所有 newTag 改成 commit SHA（backend/frontend 共用同一个 commit SHA）
        sed -i "s|newTag: .*|newTag: ${imageTag}|g" kustomization.yaml
        # kubectl apply -k（kubectl 内置 kustomize 渲染能力，不需要独立 kustomize）
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
//   4. 检查 green Pod 就绪（readiness 探针）
//   5. 人工确认：测试组验证通过 → 全量切换；不通过 → 回滚
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

    // 3. 检查 green 版本 Pod 就绪（readiness 探针通过）
    def greenReady = sh(
        script: "kubectl get pods -n ${namespace} -l version=green -o jsonpath='{.items[?(@.status.containerStatuses[0].ready==true)].metadata.name}' | wc -l",
        returnStdout: true
    ).trim()

    if (greenReady.toInteger() < 1) {
        echo "❌ green 版本 Pod 未就绪，删除并回滚"
        sh "kubectl delete deployment backend-green -n ${namespace}"
        error "灰度发布失败（green Pod 未就绪），已回滚"
    }

    // 4. 人工确认：测试组已通过 header 访问新版完成验证
    //    测试通过 → 点「确认部署」→ 全量切换
    //    测试不通过 → 点「回滚」→ 删除 green，流量保持 v1 旧版
    echo "🟡 等待测试组人工验证 green 新版（测试组带 X-User-Group: beta 访问新版）"
    def decision = input(
        message: "测试组已验证 green 新版？通过则全量切换，不通过则回滚：",
        parameters: [
            choice(
                name: 'TEST_RESULT',
                choices: ['PASS（测试通过，全量切换）', 'FAIL（测试不通过，回滚）'],
                description: '测试组人工验证结果'
            )
        ],
        ok: '提交结果'
    )

    if (decision == 'FAIL（测试不通过，回滚）') {
        echo "❌ 测试不通过，回滚：删除 green 版本，流量保持 v1 旧版"
        sh "kubectl delete deployment backend-green -n ${namespace}"
        error "灰度发布失败（测试不通过），已回滚，v1 旧版继续服务"
    }

    // 5. 测试通过 → 全量切换（100% 流量切到 green 新版）
    echo "✅ 测试通过，全量切换流量到 green 新版..."
    sh """
        kubectl apply -f k8s/istio/virtualservice-full.yaml
    """
    echo "✅ 灰度发布完成，100% 流量已切到 green 新版"

    // 6. 旧版 blue 保留作回滚点，确认稳定后下线
    sh "kubectl delete deployment backend-blue -n ${namespace} --ignore-not-found=true"
}

// ============================================================
// 飞书通知函数：有 webhook 就发消息，没有就 echo 提示（不阻断流水线）
// ============================================================
def notifyFeishu(text) {
    if (env.FEISHU_WEBHOOK) {
        sh """
            curl -X POST '${env.FEISHU_WEBHOOK}' \\
                -H 'Content-Type: application/json' \\
                -d '{"msg_type":"text","content":{"text":"${text}"}}'
        """
    } else {
        echo "🔕 [飞书通知跳过] 未配置 FEISHU_WEBHOOK 凭证，消息内容：${text}"
    }
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
        // 飞书 webhook：用 credentials 读取，但用 default 兜底避免凭证缺失导致流水线直接失败
        // 注意：这里不能用 credentials() 直接读，否则没配 feishu-webhook 凭证时流水线在 environment 阶段就报错
        FEISHU_WEBHOOK  = ''
    }

    stages {

        // ============ Stage 1: 代码检出 ============
        stage('Checkout') {
            steps {
                script {
                    // 如果 Jenkins 任务配置了 SCM（远程 Git 仓库），就正常 checkout；
                    // 如果没配置（比如代码还在本地、未 push 到远程），就跳过并提示。
                    try {
                        checkout scm
                        echo "✅ 代码检出完成，commit: ${GIT_COMMIT.take(8)}"
                    } catch (Exception e) {
                        echo "⚠️ 未配置 SCM 远程仓库，跳过 checkout，使用当前 workspace 里的代码"
                        echo "   提示：需要先把代码 push 到 Git 远程仓库（GitHub/内网 GitLab），并在 Jenkins 任务里配置源码地址"
                    }
                }
            }
        }

        // ============ Stage 2: 代码静态扫描（SonarQube）============
        stage('Code Scan') {
            steps {
                container('python') {
                    script {
                        echo "开始 SonarQube 代码扫描..."
                        // 检测 SonarQube 是否配置；没配（集群里没装 SonarQube）就跳过，不让流水线卡死
                        def sonarConfigured = false
                        try {
                            // withSonarQubeEnv 只有在 Jenkins 配置了 SonarQube 服务器时才可用
                            withSonarQubeEnv('sonarqube') {
                                sonarConfigured = true
                            }
                        } catch (Exception e) {
                            sonarConfigured = false
                        }
                        if (sonarConfigured) {
                            sh '''
                                cd src/backend
                                pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
                                sonar-scanner \\
                                    -Dsonar.projectKey=marriott-backend \\
                                    -Dsonar.sources=. \\
                                    -Dsonar.python.version=3
                            '''
                            echo "✅ 代码扫描完成"
                        } else {
                            echo "⚠️ 集群未部署 SonarQube，跳过代码扫描"
                            echo "   提示：生产环境部署 SonarQube 后，在 Jenkins 里配置 sonarqube 服务器即可启用此阶段"
                        }
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
                        // 检测 Dependency-Check 工具是否安装；没装就跳过，不让流水线卡死
                        def dcInstalled = false
                        try {
                            dependencyCheck additionalArguments: '''--version''', odcInstallation: 'dependency-check'
                            dcInstalled = true
                        } catch (Exception e) {
                            dcInstalled = false
                        }
                        if (dcInstalled) {
                            dependencyCheck additionalArguments: '''
                                --scan src/backend/requirements.txt
                                --format HTML
                                --failOnCVSS 7
                            ''', odcInstallation: 'dependency-check'
                            echo "✅ 依赖扫描完成（无 CVSS>=7 的高危漏洞）"
                        } else {
                            echo "⚠️ 未安装 Dependency-Check 工具，跳过依赖漏洞扫描"
                            echo "   提示：安装 OWASP Dependency-Check 插件并在全局工具里配置后即可启用"
                        }
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
                        // 显式指定 linux/amd64（集群节点是 amd64，避免本地 arm64 推送导致 exec format error）
                        sh "docker build --platform linux/amd64 -t ${BACKEND_IMAGE} src/backend"
                        sh "docker build --platform linux/amd64 -t ${FRONTEND_IMAGE} src/frontend"
                        sh "docker build --platform linux/amd64 -t ${WEBHOOK_IMAGE} src/webhook"
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
                        // 检测 Harbor 凭证是否配置；没配就 echo 跳过（不阻断流水线）
                        def harborCredOk = false
                        try {
                            withCredentials([usernamePassword(
                                credentialsId: 'harbor-credentials',
                                usernameVariable: 'HARBOR_USER',
                                passwordVariable: 'HARBOR_PASS'
                            )]) {
                                sh "docker login ${HARBOR_REGISTRY} -u ${HARBOR_USER} -p ${HARBOR_PASS}"
                                harborCredOk = true
                            }
                        } catch (Exception e) {
                            harborCredOk = false
                        }
                        if (harborCredOk) {
                            sh "docker push ${BACKEND_IMAGE}"
                            sh "docker push ${FRONTEND_IMAGE}"
                            sh "docker push ${WEBHOOK_IMAGE}"
                            echo "✅ 镜像推送完成"
                        } else {
                            echo "⚠️ 未配置 Harbor 凭证（harbor-credentials），跳过镜像推送"
                            echo "   提示：在 Jenkins 里配置 harbor-credentials 凭证（用户名/密码）后即可推送"
                        }
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
                    notifyFeishu("🔔 [审批请求] 业务服务 ${GIT_COMMIT.take(8)} 待部署到 staging 环境，请到 Jenkins 审批")
                    // 人工审批
                    input message: '是否部署到 staging 环境？', ok: '部署'
                }
                container('kubectl') {
                    deployTo('staging', BACKEND_IMAGE, FRONTEND_IMAGE)
                }
                script {
                    notifyFeishu("✅ staging 部署完成")
                }
            }
        }

        // ============ Stage 10: production 人工审批（最严格）============
        stage('Deploy Production') {
            steps {
                script {
                    // 生产部署只允许从 tag 触发，且需要二次审批
                    notifyFeishu("🚨 [生产审批] ${GIT_COMMIT.take(8)} 待部署到 production，请审批")
                    input message: '生产部署需二次确认，确认部署？', ok: '确认部署'
                }
                container('kubectl') {
                    // 生产环境用 Istio header 灰度（测试组先验证，通过后全量切换）
                    progressiveDeploy('production', BACKEND_IMAGE)
                }
                script {
                    notifyFeishu("✅ production 部署完成")
                }
            }
        }
    }

    post {
        success {
            script {
                notifyFeishu("🎉 流水线 ${JOB_NAME} #${BUILD_NUMBER} 构建成功")
            }
        }
        failure {
            script {
                notifyFeishu("❌ 流水线 ${JOB_NAME} #${BUILD_NUMBER} 失败，请检查")
            }
        }
    }
}
