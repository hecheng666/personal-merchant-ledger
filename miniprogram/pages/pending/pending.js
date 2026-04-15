const api = require('../../utils/api')
const fmt = require('../../utils/format')

const OPTIONS = [
  { label: '营业收款（顾客付的）', category: 'business_income' },
  { label: '进货支出（付给供货商）', category: 'business_expense' },
  { label: '私人往来（和家人朋友）', category: 'private' },
]

Page({
  data: {
    loading: false,
    total: 0,
    items: [],
  },

  onShow() {
    this.load()
  },

  load() {
    this.setData({ loading: true })
    api
      .call('/api/pending?limit=100')
      .then((res) => {
        const items = (res.items || []).map((item) => ({
          ...item,
          amount_text: fmt.amountText(item),
        }))
        this.setData({ loading: false, total: res.total, items })
      })
      .catch((err) => {
        this.setData({ loading: false })
        api.showError(err)
      })
  },

  classify(event) {
    const peer = event.currentTarget.dataset.peer
    wx.showActionSheet({
      itemList: OPTIONS.map((item) => item.label),
      success: (res) => {
        const picked = OPTIONS[res.tapIndex]
        this.save(peer, picked.category)
      },
    })
  },

  save(peer, category) {
    wx.showLoading({ title: '保存中', mask: true })
    api
      .call('/api/confirm', 'POST', { peer, category })
      .then(() => {
        wx.hideLoading()
        wx.showToast({ title: '已记住', icon: 'success' })
        this.load()
      })
      .catch((err) => {
        wx.hideLoading()
        api.showError(err)
      })
  },
})
