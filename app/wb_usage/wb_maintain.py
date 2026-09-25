# -*- coding: utf-8 -*-
r"""wb_maintain.py — 数仓维护工具（P0：raw 归档保留策略 + VACUUM + 体积报告）。

背景见 wb_collect.py「数仓维护」注释块：ods_jsonl_raw（原文归档）只写不读，
33 天堆到 ~1.7GB 占全库 84%；消费层 ods_jsonl_event 全量保留，TTL 只裁原文。

用法（默认库 = 数仓标准路径，或 --db 指定）：
  python wb_maintain.py --stats                     # 各表行数/字节 + 文件体积
  python wb_maintain.py --raw-ttl-days 14           # 清理 14 天前的原文归档
  python wb_maintain.py --raw-ttl-days 14 --vacuum  # 清理后回收文件空间
  python wb_maintain.py --vacuum                    # 仅 VACUUM（删除后手动回收）
"""
import argparse
import os
import sys
import time

import wb_collect


def mb(n):
    return f"{n / 1048576:.1f} MB" if n is not None else "-"


def show_stats(conn, db_path):
    print(f"db: {db_path}  文件体积 {mb(os.path.getsize(db_path))}")
    try:
        rows = conn.execute(
            "SELECT name, SUM(pgsize) FROM dbstat GROUP BY name ORDER BY 2 DESC"
        ).fetchall()
        print("  （dbstat 按页统计）")
        for name, sz in rows[:8]:
            print(f"  {name:24s} {mb(sz)}")
    except Exception:
        pass
    for table in ("ods_jsonl_event", "ods_jsonl_raw"):
        try:
            n, b = conn.execute(
                f"SELECT COUNT(*), SUM(LENGTH(raw_json)) FROM {table}"
                if "raw" in table else
                f"SELECT COUNT(*), SUM(LENGTH(raw_usage_json)) FROM {table}"
            ).fetchone()
            print(f"  {table:24s} {n} 行, 正文 {mb(b)}")
        except Exception as e:
            print(f"  {table}: {e}")


def main():
    ap = argparse.ArgumentParser(description="数仓维护：raw TTL / VACUUM / 体积报告")
    ap.add_argument("--db", default=wb_collect.DEFAULT_DB)
    ap.add_argument("--stats", action="store_true", help="打印各表体积报告")
    ap.add_argument("--raw-ttl-days", type=int, default=None,
                    help=f"清理 N 天前的原文归档（默认 {wb_collect.RAW_TTL_DAYS_DEFAULT}）")
    ap.add_argument("--vacuum", action="store_true", help="VACUUM 回收文件空间")
    ap.add_argument("--raw-archive", choices=("on", "off"), default=None,
                    help="原文归档总开关（off = 只记计数不存原文，T3 交付物）")
    args = ap.parse_args()

    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        print(f"db 不存在：{db_path}")
        return 1
    conn = wb_collect.get_conn(db_path)
    rc = 0
    try:
        if args.raw_archive is not None:
            wb_collect.meta_set(conn, "raw_archive",
                                "1" if args.raw_archive == "on" else "0")
            conn.commit()
            print(f"原文归档开关 → {args.raw_archive}"
                  + ("（此后采集只记计数，不写 ods_jsonl_raw）"
                     if args.raw_archive == "off" else ""))
        if args.stats or not (args.raw_ttl_days or args.vacuum):
            show_stats(conn, db_path)
        if args.raw_ttl_days is not None:
            t0 = time.time()
            deleted, freed, orphans = wb_collect.raw_retention(conn, args.raw_ttl_days)
            print(f"raw TTL({args.raw_ttl_days}d)：删 {deleted} 行，释放 {mb(freed)}，"
                  f"孤儿 {orphans}，耗时 {time.time() - t0:.1f}s"
                  + ("（VACUUM 前文件体积不变，属预期）" if not args.vacuum else ""))
        if args.vacuum:
            before = os.path.getsize(db_path)
            t0 = time.time()
            wb_collect.vacuum_db(conn)
            after = os.path.getsize(db_path)
            print(f"VACUUM：{mb(before)} → {mb(after)} "
                  f"（回收 {mb(max(0, before - after))}，耗时 {time.time() - t0:.1f}s）")
    except Exception as e:
        print(f"维护失败：{e}")
        rc = 1
    finally:
        conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
