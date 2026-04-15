// 调用云托管的统一封装。
// 小程序通过 wx.cloud.callContainer 走微信内部链路，不需要配 request 合法域名。
const config = require('../config')

function call(path, method = 'GET', data = {}) {
  return new Promise((resolve, reject) => {
    wx.cloud.callContainer({
      config: { env: config.envId },
      path,
      method,
      data,
      header: {
        'X-WX-SERVICE': config.service,
        'content-type': 'application/json',
      },
      timeout: 30000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
          return
        }
        const detail = res.data && res.data.detail
        reject(new Error(detail || `请求失败（${res.statusCode}）`))
      },
      fail(err) {
        reject(new Error((err && err.errMsg) || '网络异常，请稍后再试'))
      },
    })
  })
}

function showError(err) {
  wx.showToast({ title: (err && err.message) || '出错了', icon: 'none', duration: 2500 })
}

module.exports = { call, showError }
