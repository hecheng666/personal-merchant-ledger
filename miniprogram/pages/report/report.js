const api = require('../../utils/api')
const fmt = require('../../utils/format')

Page({
  data: {
    loading: false,
    months: [],
    monthIndex: 0,
    month: '',
    report: null,
    bars: [],
    cashDate: '',
    cashAmount: '',
    cashDirection: 'income',
  },

  onShow() {
    this.setData({ cashDate: fmt.today() })
    this.loadMonths()
  },

  loadMonths() {
    api
      .call('/api/months')
      .then((res) => {
        const months = res.months || []
        if (months.length === 0) {
          this.setData({ months: [], report: null, bars: [], loading: false })
          return
        }
        const index = this.data.month ? Math.max(months.indexOf(this.data.month), 0) : months.length - 1
        this.setData({ months, monthIndex: index, month: months[index] })
        this.loadReport(months[index])
      })
      .catch((err) => api.showError(err))
  },

  loadReport(month) {
    this.setData({ loading: true })
    api
      .call('/api/report?month=' + month)
      .then((res) => {
        const view = {
          month: res.month,
          total_tx: res.total_tx,
          gross_business: fmt.money(res.gross_business),
          refund: fmt.money(res.refund),
          wechat_net: fmt.money(res.wechat_net),
          purchase_expense: fmt.money(res.purchase_expense),
          cash_income: fmt.money(res.cash_income),
          cash_expense: fmt.money(res.cash_expense),
          revenue_total: fmt.money(res.revenue_total),
          balance: fmt.money(res.balance),
          pending_count: res.pending_count,
          pending_peers: res.pending_peers,
          pending_amount: fmt.money(res.pending_amount),
          unknown_count: res.unknown_count,
          closed_count: res.closed_count,
          private_count: res.private_count,
        }

        const values = (res.daily || []).map((item) => Number(item.wechat) + Number(item.cash))
        const max = Math.max.apply(null, values.concat([1]))
        const bars = (res.daily || []).map((item, index) => ({
          day: item.day.slice(-2),
          value: values[index],
          h: values[index] <= 0 ? 0 : Math.max(Math.round((values[index] / max) * 100), 2),
        }))

        this.setData({ loading: false, report: view, bars })
      })
      .catch((err) => {
        this.setData({ loading: false })
        api.showError(err)
      })
  },

  changeMonth(event) {
    const index = Number(event.detail.value)
    this.setData({ monthIndex: index, month: this.data.months[index] })
    this.loadReport(this.data.months[index])
  },

  bindDate(event) {
    this.setData({ cashDate: event.detail.value })
  },

  bindAmount(event) {
    this.setData({ cashAmount: event.detail.value })
  },

  setIncome() {
    this.setData({ cashDirection: 'income' })
  },

  setExpense() {
    this.setData({ cashDirection: 'expense' })
  },

  saveCash() {
    const amount = (this.data.cashAmount || '').trim()
    if (!amount) {
      wx.showToast({ title: '先填个金额', icon: 'none' })
      return
    }
    wx.showLoading({ title: '保存中', mask: true })
    api
      .call('/api/cash', 'POST', {
        day: this.data.cashDate,
        amount,
        direction: this.data.cashDirection,
      })
      .then(() => {
        wx.hideLoading()
        wx.showToast({ title: '已记下', icon: 'success' })
        this.setData({ cashAmount: '' })
        this.loadReport(this.data.month)
      })
      .catch((err) => {
        wx.hideLoading()
        api.showError(err)
      })
  },
})
