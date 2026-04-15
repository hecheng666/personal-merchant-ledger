const config = require('./config')

App({
  onLaunch() {
    if (!wx.cloud) {
      wx.showModal({
        title: '基础库版本过低',
        content: '请把微信开发者工具的调试基础库调到 2.2.3 以上',
        showCancel: false,
      })
      return
    }
    wx.cloud.init({
      env: config.envId,
      traceUser: true,
    })
  },
})
