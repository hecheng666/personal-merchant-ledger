const api = require('../../utils/api')
const fmt = require('../../utils/format')

Page({
  data: {
    loading: false,
    fileName: '',
    preview: null,
    saved: null,
  },

  // 第一步：选文件并让后端预览，不落库
  chooseFile() {
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['csv', 'xlsx'],
      success: (res) => {
        const file = res.tempFiles[0]
        this.fileName = file.name
        wx.showLoading({ title: '读取中', mask: true })
        // 统一按 base64 读，后端自己区分 CSV 和 xlsx
        wx.getFileSystemManager().readFile({
          filePath: file.path,
          encoding: 'base64',
          success: (readRes) => {
            this.fileContent = readRes.data
            this.preview()
          },
          fail: () => {
            wx.hideLoading()
            api.showError(new Error('读取文件失败，请确认文件没有损坏'))
          },
        })
      },
      fail: (err) => {
        if (err && err.errMsg && err.errMsg.indexOf('cancel') >= 0) return
        api.showError(new Error('没有选中文件'))
      },
    })
  },

  preview() {
    this.setData({ loading: true, saved: null })
    api
      .call('/api/import', 'POST', {
        filename: this.fileName,
        content_base64: this.fileContent,
        dry_run: true,
      })
      .then((res) => {
        wx.hideLoading()
        this.setData({
          loading: false,
          preview: res.stats,
          fileName: this.fileName,
        })
      })
      .catch((err) => {
        wx.hideLoading()
        this.setData({ loading: false })
        api.showError(err)
      })
  },

  // 第二步：确认导入，后端按交易单号去重，重复导入不会翻倍
  confirmImport() {
    if (!this.fileContent) return
    this.setData({ loading: true })
    wx.showLoading({ title: '导入中', mask: true })
    api
      .call('/api/import', 'POST', {
        filename: this.fileName,
        content_base64: this.fileContent,
      })
      .then((res) => {
        wx.hideLoading()
        this.setData({ loading: false, preview: null, saved: res })
        wx.showToast({ title: '导入完成', icon: 'success' })
      })
      .catch((err) => {
        wx.hideLoading()
        this.setData({ loading: false })
        api.showError(err)
      })
  },

  goPending() {
    wx.switchTab({ url: '/pages/pending/pending' })
  },

  goReport() {
    wx.switchTab({ url: '/pages/report/report' })
  },
})
