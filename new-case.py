#!/usr/bin/env python3
"""new-case.py —— 开一份新案卷（照模板生成，占好编号）。

用法：python new-case.py "一句话形状" [--class 类名]
它做三件事：找一个没用过的编号 → 写 cases/NNNN-slug.md → 在 CASES.md 里加一行。
**不替你写内容**：模板里的"实例/复现/控制对"必须填真东西，空着就等于没有这份案卷。
"""
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# 2026-09-20 加：出口编码必须自己钉住（默认代码页 GBK 时，打印非 ASCII 会直接死在这一行）。
# 同时挡住 pythonw（计划任务）下 sys.stdout 为 None 的情况。类级闸门见 check_0014 臂⑥。
for _s in (sys.stdout, sys.stderr):
    if _s is not None:
        try:
            _s.reconfigure(encoding='utf-8')
        except Exception:                                             # noqa: BLE001
            pass

HERE = Path(__file__).parent
CASES = HERE / 'cases'

TEMPLATE = """# {num} — {title}

**类**：{cls} ｜ **状态**：待复现（还没实现 check()）｜ **复查日期**：{recheck}

## 形状

（一句话：什么被误读成了什么。写不出这一句就说明还没想清。）

## 实例

（日期 + 出处文件/id + 当时的读数 + **是谁当场抓出来的**。抓的人不是你也要写。）

## 复现

```
python run-all.py --only {num}
```
（说明这条 check 读什么、判据是什么；语料不在本机时应当报"测不了"，**不报"已修复"**。）

## 控制对

- **正控制**：（必须报警的那个信号）
- **负控制**：（必须不报警的那个信号；它不亮才说明正控制有意义）

## 地基（不经检验就信任的东西）

（2026-09-20 加，longcat 指出：**任何一个自证的方案，都有一个必须从外面验的基例**。
这里写这条检查**没有验证、直接信任**的东西——调度器？文件系统？时间源？解释器？网络？
写出来它才叫"由构造信任"；不写，它就默认"已经被验证过了"，而那是假的。

**上次确认这个信任仍然成立：<日期>** —— 这一行是 longcat 当天补的：名字不够，
一个写下来的地基**没有复查日期**，就会变成"第二种未验证的假设"，而且带着
"已经被记录过"的假权威。判据：*这个信任上一次被实际确认是什么时候？*）

## 检测规则

（在野怎么发现它，一句可执行的话。）

## 同类

（这条和哪几条同一个形状。归不进已有类才开新类。）
"""


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    title = sys.argv[1]
    cls = sys.argv[sys.argv.index('--class') + 1] if '--class' in sys.argv else '未分类'
    used = sorted(int(m.group(1)) for p in CASES.glob('*.md')
                  if (m := re.match(r'(\d{4})-', p.name)))
    num = f'{(used[-1] + 1) if used else 1:04d}'
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower())[:40].strip('-') or 'case'
    path = CASES / f'{num}-{slug}.md'
    path.write_text(TEMPLATE.format(num=num, title=title, cls=cls,
                                    recheck=(date.today() + timedelta(days=7)).isoformat()),
                    encoding='utf-8')
    idx = HERE / 'CASES.md'
    row = (f'| [{num}](cases/{path.name}) | {title} | 待复现 | '
           f'{(date.today() + timedelta(days=7)).isoformat()} | {cls} |\n')
    text = idx.read_text(encoding='utf-8')
    if not text.endswith('\n'):
        text += '\n'
    idx.write_text(text + row, encoding='utf-8')
    print(f'已建 {path}')
    print(f'已在 CASES.md 加一行')
    print('提醒：模板里的实例/复现/控制对必须填真东西，并在 run-all.py 里加一个 check()。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
