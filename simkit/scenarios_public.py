"""
公开榜实例集：与开发集同族参数、不同种子。
用途：OJ 公开榜评测（防止选手对开发集过拟合）。
正式评测集（隐藏族 + 保密种子）不在此文件。
"""
from simkit.scenarios import _f2_instance, _f3_instance, _f6_instance

# 公开榜：3 族 × 3 种子，种子段 5xx 与开发集 1xx/2xx/3xx 不重叠
PUBLIC_F2 = [_f2_instance(s) for s in [501, 502, 503]]
PUBLIC_F3 = [_f3_instance(s) for s in [504, 505, 506]]
PUBLIC_F6 = [_f6_instance(s) for s in [507, 508, 509]]

# OJ 内部使用不暴露 family 标签的版本（评分用，榜上只显示族均分）
PUBLIC_INSTANCES = PUBLIC_F2 + PUBLIC_F3 + PUBLIC_F6
