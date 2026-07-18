# Host text heuristics (no voiceprint)

## Goal

Build host_transcript for product talk. Reject cohost/system/pure chat when possible.

## KEEP signals

- product demo language: 来看这件, 上身, 面料给你看
- fit/fabric/selling_point/price language
- livestream CTA / 挂车: 小黄车, 1号链接, 2号链接, 号链接, 弹窗, 加购, 下单, 领券, 购物车
- first-person try-on: 我身上, 看我腰

## REJECT reasons

| reason | signals |
|--------|---------|
| cohost | 老板说得对, 给老板点赞 |
| system_or_spam | 谢谢舰, 欢迎来到直播间 spam chains |
| chitchat | 听得到吗, 家人们在吗 with no product |
| off_product | read-comment logistics only, no clothing claims |
| hard_exclude | size / pure sentiment / pure chitchat |

## Decision order

1. hard_exclude → reject from clipper path (log excluded.json)
2. strong non-host and no keep-type → reject
3. strong host or keep-type → keep
4. ambiguous → neighborhood vote ±2 utterances; still ambiguous → reject

## Degraded mode

If almost everything would reject or no host signal exists: keep all non-hard-exclude lines, set host_filter=degraded in run_report.

## Report fields

kept_count, rejected_count, rejected_ratio, degraded, top_reject_reasons
