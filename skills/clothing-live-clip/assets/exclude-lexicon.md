# Hard-exclude lexicon (substring match, Chinese)

## size (never in clipper transcript / plan)

尺码, 选码, 偏大, 偏小, 胸围, 腰围, 臀围, 身高, 穿M, 穿S, 穿L, 穿XL, 均码, 加大码, 码数, 建议穿

## sentiment pure (never in clipper transcript / plan if no core claim co-occurs)

做了五年, 不容易, 感谢陪伴, 创业, 初心, 故事是这样, 一路走来, 谢谢支持我, 喜欢我的人

## chitchat pure

家人们, 老铁们, 听得到吗, 扣1, 扣一, 点点关注, 双击, 晚上好啊, 来了吗

## Notes

- If line also contains fit/fabric/selling_point/price core keywords, do NOT pure-exclude (mixed_keep).
- **Never put livestream CTA here:** 小黄车, N号链接, 弹窗, 加购, 下单, 购物车 → these are **price keep**, not exclude.
- Expand only via confirmed cases + regression.
