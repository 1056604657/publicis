// ============================================================
// Marriott 业务服务 CI/CD 流水线（Jenkins Declarative Pipeline）
//
// 企业级完整流水线，包含以下阶段：
//   1. Checkout          代码检出
//   2. Code Scan         代码静态扫描（SonarQube）
//   3. Unit Test         单元测试
//   4. E2E Test          端到端测试（docker-compose 拉起三层应用，curl 验证）
//   5. Dependency Scan   依赖漏洞扫描（Dependency-Check）
//   6. Build Image       构建镜像（多阶段构建）
//   7. Image Scan        镜像合规检查（Trivy 高危漏洞 + 非 root）
//   8. Push Image        推送到内网 Harbor
//   9. Deploy Dev/Test/Perf   自动部署
//  10. Deploy Staging/Prod    人工审批 + 飞书通知
//
// ============================================================

// ============================================================
def deployTo(envName, backendImage, frontendImage) {
    def imageTag = backendImage.tokenize(':')[-1]
    container('python') {
        sh """
            pip install -q -i https://mirrors.aliyun.com/pypi/simple/ ruamel.yaml
            python scripts/update-image-tag.py k8s/overlays/${envName}/kustomization.yaml \\
                "hub-sh.aijidou.com/base/marriott-backend=${imageTag}" \\
                "hub-sh.aijidou.com/base/marriott-frontend=${imageTag}"
        """
    }
    container('kubectl') {
        sh "kubectl apply -k k8s/overlays/${envName}"
    }
    commitAndPush("部署 ${envName}：镜像更新到 ${imageTag}")
}

def commitAndPush(message) {
    def branchName = (env.GIT_BRANCH ?: 'origin/main').tokenize('/').last()
    withCredentials([usernamePassword(
        credentialsId: 'github-credentials',
        usernameVariable: 'GIT_USER',
        passwordVariable: 'GIT_PASSWORD'
    )]) {
        sh """
            git config user.email "marriott-ci@example.com"
            git config user.name "Marriott CI"
            if [ -n "${env.GIT_HTTP_PROXY ?: ''}" ]; then
                git config http.proxy "${env.GIT_HTTP_PROXY}"
            fi
            git add k8s/overlays
            git commit -m "${message} [ci skip]" || echo "⚠️ 没有变更需要提交（tag 未变化），跳过 push"
            git -c credential.helper='!f() { echo username=\$GIT_USER; echo password=\$GIT_PASSWORD; }; f' push origin ${branchName}
        """
    }
}

def progressiveDeploy(envName, backendImage) {
    def namespace = "marriott-${envName}"

    container('kubectl') {
        sh """
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
        """
    }

    container('kubectl') {
        sh """
            kubectl wait --for=condition=ready \\
                pod -l app=backend,version=green \\
                -n ${namespace} \\
                --timeout=300s
        """
    }

    def greenReady = sh(
        script: "kubectl get pods -n ${namespace} -l version=green -o jsonpath='{.items[?(@.status.containerStatuses[0].ready==true)].metadata.name}' | wc -l",
        returnStdout: true
    ).trim()

    if (greenReady.toInteger() < 1) {
        echo "❌ green 版本 Pod 未就绪，删除并回滚"
        container('kubectl') {
            sh "kubectl delete deployment backend-green -n ${namespace} --ignore-not-found=true"
        }
        error "灰度发布失败（green Pod 未就绪），已回滚"
    }

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
        container('kubectl') {
            sh "kubectl delete deployment backend-green -n ${namespace}"
        }
        error "灰度发布失败（测试不通过），已回滚，v1 旧版继续服务"
    }

    echo "✅ 测试通过，全量切换流量到 green 新版..."
    container('kubectl') {
        sh """
            kubectl apply -f k8s/istio/virtualservice-full.yaml
        """
    }
    echo "✅ 灰度发布完成，100% 流量已切到 green 新版"

    echo "✅ 灰度发布完成，开始角色轮换..."
    def newTag = backendImage.tokenize(':')[-1]
    container('python') {
        sh """
            pip install -q -i https://mirrors.aliyun.com/pypi/simple/ ruamel.yaml
            python scripts/update-image-tag.py k8s/overlays/${envName}/kustomization.yaml \\
                "hub-sh.aijidou.com/base/marriott-backend=${newTag}"
        """
    }
    container('kubectl') {
        sh "kubectl apply -k k8s/overlays/${envName}"
    }
    commitAndPush("角色轮换 ${envName}：blue 更新到 ${newTag}")
    container('kubectl') {
        sh "kubectl apply -f k8s/istio/virtualservice.yaml"
    }
    container('kubectl') {
        sh "kubectl delete deployment backend-green -n ${namespace} --ignore-not-found=true"
    }
    echo "✅ 角色轮换完成：backend（blue）已更新为新版本，green 已清理"
    echo "   下次灰度发布时：backend=blue（当前稳定版），新部署的 backend-green=新版"
}

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
    image: hub-sh.aijidou.com/base/docker:24-amd64
    command: ['sleep', 'infinity']
    volumeMounts:
    - name: docker-sock
      mountPath: /var/run/docker.sock
  - name: python
    image: hub-sh.aijidou.com/base/python:3.12-slim-amd64
    command: ['sleep', 'infinity']
  - name: kubectl
    image: hub-sh.aijidou.com/base/kubectl:latest-amd64
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
        FEISHU_WEBHOOK  = ''
    }

    stages {

        // ============ Stage 1: 代码检出 ============
        stage('Checkout') {
            steps {
                script {
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

        // ============ Stage 2: 代码静态扫描 ============
        stage('Code Scan') {
            when {
                not { environment name: 'SKIP_SONAR', value: 'true' }
            }
            steps {
                container('python') {
                    script {
                        echo "开始 SonarQube 代码扫描..."
                        withSonarQubeEnv('sonarqube') {
                            sh '''
                                cd src/backend
                                pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
                                sonar-scanner \\
                                    -Dsonar.projectKey=marriott-backend \\
                                    -Dsonar.sources=. \\
                                    -Dsonar.python.version=3
                            '''
                        }
                        timeout(time: 5, unit: 'MINUTES') {
                            def qg = waitForQualityGate()
                            if (qg.status != 'OK') {
                                error "❌ SonarQube Quality Gate 失败：${qg.status}"
                            }
                        }
                        echo "✅ 代码扫描完成（Quality Gate OK）"
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

        // ============ Stage 4: 端到端测试（docker-compose 三层链路）============
        stage('E2E Test') {
            when {
                not { environment name: 'SKIP_E2E', value: 'true' }
            }
            steps {
                container('docker') {
                    script {
                        echo "开始 E2E 测试（docker-compose 拉起三层应用）..."
                        sh '''
                            set -e
                            cd ${WORKSPACE}
                            # 拉起三层应用（后端依赖 DB/Redis 健康才能 start）
                            docker-compose up -d
                            # 等应用就绪（最多等 90 秒，避免硬编码 sleep）
                            for i in $(seq 1 18); do
                                if curl -fsS http://localhost:8080/healthz >/dev/null 2>&1; then
                                    echo "✅ backend 已就绪（第 ${i} 次探测）"
                                    break
                                fi
                                echo "⏳ 等待 backend 就绪... ${i}/18"
                                sleep 5
                            done
                            # 1. 存活探针
                            curl -fsS http://localhost:8080/healthz | grep -q '"status":"ok"' || { echo "❌ /healthz 失败"; exit 1; }
                            # 2. 就绪探针（验证 DB + Redis 连通）
                            curl -fsS http://localhost:8080/readyz  | grep -q '"status":"ready"' || { echo "❌ /readyz 失败"; exit 1; }
                            # 3. 缓存链路（第一次 database，第二次 cache——这是核心业务逻辑）
                            FIRST=$(curl -fsS http://localhost:8080/api/items | grep -o '"source":"[^"]*"' | head -1)
                            SECOND=$(curl -fsS http://localhost:8080/api/items | grep -o '"source":"[^"]*"' | head -1)
                            echo "第一次请求: ${FIRST}    第二次请求: ${SECOND}"
                            # 至少有一次是 cache（命中缓存验证 Redis 工作正常）
                            echo "${FIRST} ${SECOND}" | grep -q 'cache' || { echo "❌ 缓存未命中，Redis 链路异常"; exit 1; }
                            # 清理（保留 -v 避免磁盘残留）
                            docker-compose down -v
                            echo "✅ E2E 测试通过（健康检查 + 缓存链路验证）"
                        '''
                    }
                }
            }
        }

        // ============ Stage 5: 依赖漏洞扫描（Dependency-Check）============
        stage('Dependency Scan') {
            steps {
                container('python') {
                    script {
                        echo "开始依赖漏洞扫描..."
                        dependencyCheck additionalArguments: '''
                            --scan src/backend/requirements.txt
                            --format HTML
                            --format XML
                            --failOnCVSS 7
                            --failOnCannotParse
                        ''', odcInstallation: 'dependency-check'
                        dependencyCheckPublisher pattern: '**/dependency-check-report.xml'
                        echo "✅ 依赖扫描完成（无 CVSS>=7 的高危漏洞）"
                    }
                }
            }
        }

        // ============ Stage 6: 构建镜像 ============
        stage('Build Image') {
            steps {
                container('docker') {
                    script {
                        echo "开始构建三个镜像..."
                        sh "docker build --platform linux/amd64 -t ${BACKEND_IMAGE} src/backend"
                        sh "docker build --platform linux/amd64 -t ${FRONTEND_IMAGE} src/frontend"
                        sh "docker build --platform linux/amd64 -t ${WEBHOOK_IMAGE} src/webhook"
                        echo "✅ 镜像构建完成"
                    }
                }
            }
        }

        // ============ Stage 7: 镜像合规检查（Trivy + 非 root）============
        stage('Image Scan') {
            steps {
                container('docker') {
                    script {
                        echo "开始镜像合规检查..."

                        sh '''
                            docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
                                hub-sh.aijidou.com/base/trivy:latest-amd64 image --severity HIGH,CRITICAL --exit-code 1 \
                                ${BACKEND_IMAGE} || {
                                echo "❌ 后端镜像存在高危漏洞，构建中止"
                                exit 1
                            }
                        '''
                        sh '''
                            docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
                                hub-sh.aijidou.com/base/trivy:latest-amd64 image --severity HIGH,CRITICAL --exit-code 1 \
                                ${FRONTEND_IMAGE} || exit 1
                        '''
                        sh '''
                            docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
                                hub-sh.aijidou.com/base/trivy:latest-amd64 image --severity HIGH,CRITICAL --exit-code 1 \
                                ${WEBHOOK_IMAGE} || exit 1
                        '''

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

        // ============ Stage 8: 推送镜像到 Harbor ============
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

        // ============ Stage 9: 自动部署 dev/test/perf ============
        stage('Deploy Dev/Test/Perf') {
            steps {
                script {
                    echo "自动部署到 dev/test/perf 环境..."
                    deployTo('dev', BACKEND_IMAGE, FRONTEND_IMAGE)
                    deployTo('test', BACKEND_IMAGE, FRONTEND_IMAGE)
                    deployTo('perf', BACKEND_IMAGE, FRONTEND_IMAGE)
                    echo "✅ dev/test/perf 自动部署完成"
                }
            }
        }

        // ============ Stage 10: staging 人工审批 + 飞书通知 ============
        stage('Deploy Staging') {
            steps {
                script {
                    notifyFeishu("🔔 [审批请求] 业务服务 ${GIT_COMMIT.take(8)} 待部署到 staging 环境，请到 Jenkins 审批")
                    input message: '是否部署到 staging 环境？', ok: '部署'
                    deployTo('staging', BACKEND_IMAGE, FRONTEND_IMAGE)
                    notifyFeishu("✅ staging 部署完成")
                }
            }
        }

        // ============ Stage 11: production 人工审批（最严格）============
        stage('Deploy Production') {
            steps {
                script {
                    notifyFeishu("🚨 [生产审批] ${GIT_COMMIT.take(8)} 待部署到 production，请审批")
                    input message: '生产部署需二次确认，确认部署？', ok: '确认部署'
                    progressiveDeploy('production', BACKEND_IMAGE)
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
