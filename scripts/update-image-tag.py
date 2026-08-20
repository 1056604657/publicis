#!/usr/bin/env python3
"""
结构化更新 kustomization.yaml 的镜像 tag（替代 sed 文本替换）。

用法：
    python scripts/update-image-tag.py <kustomization.yaml> [<镜像名>=<newTag> ...]

示例：
    python scripts/update-image-tag.py k8s/overlays/dev/kustomization.yaml \
        hub-sh.aijidou.com/base/marriott-backend=abc12345 \
        hub-sh.aijidou.com/base/marriott-frontend=abc12345

为什么不用 sed：
    1. sed "s|newTag: .*|newTag: xx|g" 会一把梭替换所有 newTag，backend/frontend 隐式耦合
    2. sed 依赖 YAML 行序（如 "/marriott-backend/,+1"），格式一变就静默改错
    3. 这个脚本按 images[].newName 精确匹配，只改指定的镜像，且保留文件注释和格式
"""
import sys

from ruamel.yaml import YAML


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    path = sys.argv[1]
    # 解析 name=tag 参数
    updates = {}
    for arg in sys.argv[2:]:
        name, _, tag = arg.partition("=")
        if name and tag:
            updates[name] = tag

    yaml = YAML()
    yaml.preserve_quotes = True  # 保留原有引号风格
    # 缩进风格与原 kustomization.yaml 一致（mapping=2, sequence=4, offset=2），
    # 保证 dump 后除了目标行没有任何格式变化（git diff 干净，可审计）
    yaml.indent(mapping=2, sequence=4, offset=2)

    with open(path) as f:
        data = yaml.load(f)

    images = data.get("images") or []
    changed = []
    for img in images:
        # overlay 里是 name + newTag（name 为完整镜像名），base 里是 name + newName
        # 两个字段都匹配，保证无论哪个层级都能精确命中
        key = img.get("newName") or img.get("name")
        if key in updates and img.get("newTag") != updates[key]:
            img["newTag"] = updates[key]
            changed.append(f"{key}:{updates[key]}")

    if not changed:
        print(f"无变更（{path} 的 tag 已是最新）")
        return

    with open(path, "w") as f:
        yaml.dump(data, f)
    print(f"已更新 {path}:")
    for c in changed:
        print(f"  {c}")


if __name__ == "__main__":
    main()
