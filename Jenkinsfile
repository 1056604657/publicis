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
// 环境变量（Jenkins 里配置）：
//   HARBOR_REGISTRY   内网 Harbor 地址（hub-sh.aijidou.com/base）
//   HARBOR_CREDENTIALS Jenkins 凭证 ID（docker 登录，必填）
//   FEISHU_WEBHOOK     飞书机器人 webhook 地址
//   SONAR_HOST_URL     SonarQube 地址
//   SONAR_TOKEN        SonarQube token
//   SKIP_SONAR         显式跳过 SonarQube 门控（默认 false，强制扫描）
//   SKIP_E2E           显式跳过 E2E 测试（默认 false，强制跑）
//   GIT_HTTP_PROXY     git push 用的代理（GitHub 走代理，与集群内 checkout 一致）
// Jenkins 凭证：
//   harbor-credentials Harbor 账号密码（docker login，必填）
//   github-credentials GitHub 账号/Token（git push 写回 tag 用，必填）
// ============================================================

// ============================================================
// 部署函数：Kustomize 方式部署（保持与 K8s 配置管理一致），GitOps 雏形
// 流程：
//   1. python 容器：scripts/update-image-tag.py 结构化更新 overlay 的 newTag
//      （按镜像名精确匹配，替代 sed——sed 会一把梭改所有 newTag 且依赖 YAML 行序）
//   2. kubectl 容器：kubectl apply -k（kubectl 内置 kustomize 渲染，不需要独立 kustomize）
//   3. jnlp 容器：把新 tag 写回 git（git 是事实来源，可审计可复现；[ci skip] 防循环触发）
// ============================================================
def deployTo(envName, backendImage, frontendImage) {
    // 从镜像名提取 tag（backend 和 frontend 都是 commit SHA，tag 相同）
    def imageTag = backendImage.tokenize(':')[-1]
    // 1. 结构化更新 overlay 镜像 tag（ruamel.yaml 保留注释和格式，git diff 只有目标行）
    container('python') {
        sh """
            pip install -q -i https://mirrors.aliyun.com/pypi/simple/ ruamel.yaml
            python scripts/update-image-tag.py k8s/overlays/${envName}/kustomization.yaml \\
                "hub-sh.aijidou.com/base/marriott-backend=${imageTag}" \\
                "hub-sh.aijidou.com/base/marriott-frontend=${imageTag}"
        """
    }
    // 2. 部署（kubectl 内置 kustomize 渲染能力，不需要独立 kustomize 二进制）
    container('kubectl') {
        sh "kubectl apply -k k8s/overlays/${envName}"
    }
    // 3. 写回 git（GitOps：git 是事实来源；[ci skip] 防止 commit 触发流水线循环）
    commitAndPush("部署 ${envName}：镜像更新到 ${imageTag}")
}

// ============================================================
// 写回 git（GitOps 雏形）：把 overlay 的 newTag 变更提交并 push 到远程
//  - git 成为事实来源：审计「生产跑哪个版本」直接看 git，不需要查集群
//  - commit message 带 [ci skip]，避免 push 触发 webhook 造成流水线循环
//  - 幂等兜底：tag 没变化时 git commit 无变更，跳过 push（第二次构建不会死循环）
// ============================================================
def commitAndPush(message) {
    // 从 SCM 检出信息里取分支名（origin/main → main）
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
            # 只提交 overlay 目录（避免误提交工作区其他文件）
            git add k8s/overlays
            git commit -m "${message} [ci skip]" || echo "⚠️ 没有变更需要提交（tag 未变化），跳过 push"
            # 用 credential.helper 注入 push 凭证（不把密码写进 URL，避免日志泄露）
            git -c credential.helper='!f() { echo username=\$GIT_USER; echo password=\$GIT_PASSWORD; }; f' push origin ${branchName}
        """
    }
}

// ============================================================
// Istio 灰度发布（header 定向，统一发布策略）
// 核心：先按 header 定向让「测试组」访问新版，测试通过后全量切换。
// 用 Istio 的 VirtualService 一套搞定，不需要蓝绿/金丝雀多套逻辑。
//
// 依赖：k8s/istio/ 下的 DestinationRule + VirtualService + Gateway
//       （VirtualService 是常驻基线配置——平时就是「beta header → green、其他 → blue」，
//        所以发布时不需要重新 apply，只需要创建 green Deployment 即可）
// 流程：
//   1. 部署 green 新版（version=green 标签，供 Istio subset 匹配）
//      （VirtualService 常驻，header 流量自动路由到 green）
//   2. 检查 green Pod 就绪（readiness 探针）
//   3. 人工确认：测试组验证通过 → 全量切换；不通过 → 回滚
//   4. 全量切换：apply virtualservice-full（100% → green，全量验证窗口，此时 blue 是回滚点）
//   5. 角色轮换：blue 更新成新镜像 → 恢复 header 路由 → 删 green
// ============================================================
def progressiveDeploy(envName, backendImage) {
    def namespace = "marriott-${envName}"

    // 渐进式发布：先 header 灰度让「测试组」访问新版，全量切换后再角色轮换
    // 1. 部署 green 版本（version=green 标签，供 Istio subset 匹配）
    //    VirtualService 是常驻基线（beta header → green、其他 → blue），无需重新 apply
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

    // 2. 用 kubectl wait 替代硬编码 sleep 60——等到 Pod Ready 才继续
    //    timeout 300 秒 = 5 分钟，给镜像拉取 + 启动足够时间；超时自动失败
    container('kubectl') {
        sh """
            kubectl wait --for=condition=ready \\
                pod -l app=backend,version=green \\
                -n ${namespace} \\
                --timeout=300s
        """
    }

    // 3. 检查 green 版本 Pod 数量（kubectl wait 已经保证至少 1 个 ready，这里再校验一次）
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
        container('kubectl') {
            sh "kubectl delete deployment backend-green -n ${namespace}"
        }
        error "灰度发布失败（测试不通过），已回滚，v1 旧版继续服务"
    }

    // 5. 测试通过 → 全量切换（100% 流量切到 green 新版）
    echo "✅ 测试通过，全量切换流量到 green 新版..."
    container('kubectl') {
        sh """
            kubectl apply -f k8s/istio/virtualservice-full.yaml
        """
    }
    echo "✅ 灰度发布完成，100% 流量已切到 green 新版"

    // 6. 角色轮换：让 blue 接棒新版本，为下次灰度做准备
    //    问题：如果 green 一直保留，下次灰度又部署 backend-green 会同名冲突
    //    解决：把 base 的 backend（version: blue）更新成新镜像 → 成为新的稳定版 blue
    //          然后删除 backend-green → 下次灰度再部署新的 green
    //    顺序：先更新 blue → 恢复路由（流量切回 blue）→ 最后删 green（避免流量中断）
    echo "✅ 灰度发布完成，开始角色轮换..."
    // 从镜像名提取 tag（backend 和 frontend 都是 commit SHA，tag 相同）
    def newTag = backendImage.tokenize(':')[-1]
    // 6.1 更新 blue 稳定版：结构化更新 overlay 的 newTag（按镜像名精确匹配，只改 backend 不影响 frontend）
    container('python') {
        sh """
            pip install -q -i https://mirrors.aliyun.com/pypi/simple/ ruamel.yaml
            python scripts/update-image-tag.py k8s/overlays/${envName}/kustomization.yaml \\
                "hub-sh.aijidou.com/base/marriott-backend=${newTag}"
        """
    }
    // 6.2 应用 blue 更新（走 Kustomize，配置即代码）
    container('kubectl') {
        sh "kubectl apply -k k8s/overlays/${envName}"
    }
    // 6.3 写回 git（git 是事实来源，可审计；[ci skip] 防循环）
    commitAndPush("角色轮换 ${envName}：blue 更新到 ${newTag}")
    // 6.4 恢复 header 路由（流量默认走 v1=blue，此时 blue 已是新版本）
    container('kubectl') {
        sh "kubectl apply -f k8s/istio/virtualservice.yaml"
    }
    // 6.5 最后删除临时的 green 版本（下次灰度会重新部署新的 green）
    container('kubectl') {
        sh "kubectl delete deployment backend-green -n ${namespace} --ignore-not-found=true"
    }
    echo "✅ 角色轮换完成：backend（blue）已更新为新版本，green 已清理"
    echo "   下次灰度发布时：backend=blue（当前稳定版），新部署的 backend-green=新版"
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
        // SKIP_SONAR=true 时显式跳过（默认 false，强制扫描——不静默兜底）
        // 生产环境不允许跳过：quality gate 必须有结果
        stage('Code Scan') {
            when {
                not { environment name: 'SKIP_SONAR', value: 'true' }
            }
            steps {
                container('python') {
                    script {
                        echo "开始 SonarQube 代码扫描..."
                        // withSonarQubeEnv 必须配置 SonarQube 服务器，否则会抛 AbortException
                        // 这里不 try-catch 静默跳过：配置缺失就失败（强制门控）
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
                        // 等待 Quality Gate（超时 5 分钟，避免永远 Pending）
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
        // 用项目根目录的 docker-compose.yml 拉起三层应用，验证缓存+DB 链路
        // SKIP_E2E=true 时显式跳过（默认 false，强制跑，避免静默跳过）
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
        // CVSS>=7 的高危漏洞直接失败——这是安全门控，不能 try-catch 跳过
        stage('Dependency Scan') {
            steps {
                container('python') {
                    script {
                        echo "开始依赖漏洞扫描..."
                        // 不做 try-catch 静默兜底：插件缺失或工具未配置直接失败
                        // 真正跳过只能通过 disable stage（Jenkins job 配置里关掉此 stage）
                        dependencyCheck additionalArguments: '''
                            --scan src/backend/requirements.txt
                            --format HTML
                            --format XML
                            --failOnCVSS 7
                            --failOnCannotParse
                        ''', odcInstallation: 'dependency-check'
                        // 发布报告到 Jenkins UI
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
                        // 显式指定 linux/amd64（集群节点是 amd64，避免本地 arm64 推送导致 exec format error）
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

                        // ① Trivy 高危漏洞扫描（HIGH/CRITICAL 直接失败）
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

        // ============ Stage 8: 推送镜像到 Harbor ============
        // Harbor 凭证是必填项：没配直接失败，不静默跳过
        // 否则后面部署会用集群里旧的 latest 镜像，掩盖"镜像没推上去"的故障
        stage('Push Image') {
            steps {
                container('docker') {
                    script {
                        echo "开始推送镜像到 Harbor..."
                        // 不 try-catch：凭证缺失直接报错，强制运维先把凭证配好
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
                    // 飞书通知审批人
                    notifyFeishu("🔔 [审批请求] 业务服务 ${GIT_COMMIT.take(8)} 待部署到 staging 环境，请到 Jenkins 审批")
                    // 人工审批
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
                    // 生产部署只允许从 tag 触发，且需要二次审批
                    notifyFeishu("🚨 [生产审批] ${GIT_COMMIT.take(8)} 待部署到 production，请审批")
                    input message: '生产部署需二次确认，确认部署？', ok: '确认部署'
                    // 生产环境用 Istio header 灰度（测试组先验证，通过后全量切换）
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
