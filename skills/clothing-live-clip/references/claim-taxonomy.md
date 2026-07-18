# Claim taxonomy

## Keep types (may enter timeline)

| type | meaning | timeline role |
|------|---------|---------------|
| fit | 版型 | golden preferred / trust |
| fabric | 布料材质手感功能 | golden preferred / trust |
| selling_point | 特点痛点效果人群 | golden core |
| detail | 设计细节 | trust |
| scene | 场景 | trust |
| outfit | 搭配 | trust |
| price | 价格优惠CTA | cta; strong price may compete golden |

Every claim MUST map to transcript t0_ms–t1_ms.

## Hard exclude types (never enter transcript_for_clipper)

| type | rule |
|------|------|
| size | size lexicon or size-advice intent |
| sentiment | emotional/brand story without core keep-type payload |
| chitchat | pure interaction / hello / 扣1 with no product claim |

## Mixed line rule

If sentiment-like words AND any keep type signal in same utterance → KEEP whole line, tag keep types, optional label mixed_keep in cases. Do not drop.

## Priority for selection

selling_point > fit/fabric combo > strong price > detail/scene/outfit

## Core keyword hints (non-exhaustive)

- fit: 收腰, 修身, oversize, A字, 廓形, 高腰, 版型
- fabric: 醋酸, 凉感, 雪纺, 羊毛, 纯棉, 面料, 透气, 垂感
- selling_point: 显瘦, 遮肉, 梨形, 闭眼入, 显腿长, 不挑人
- price: 券后, 只要, 原价, 链接, 库存, 拍下
- price livestream CTA (专属直播挂车，进 CTA/可竞黄金): 小黄车, 购物车, 1号链接, 2号链接, 3号链接, 号链接, 几号链接, 弹窗, 挂上车, 上车了, 加购, 下单, 领券, 福袋, 专属价, 直播价, 到手价, 拍链接, 点链接, 戳链接, 上方链接, 下方链接

## Livestream CTA notes

- 「1号链接 / 小黄车 / 弹窗」= **price** keep-type，**不是** chitchat，**不是** hard-exclude。
- 纯方位控场「看左上角关注」无商品/无链接号 → 仍可 chitchat；「去小黄车1号链接」→ price KEEP。
- 与核心卖点同句（「收腰显瘦去1号链接」）→ 可进黄金或 CTA，优先保留完整句。
 
## Changelog 
- 2026-07-18: add livestream CTA keywords under price keep-type 
