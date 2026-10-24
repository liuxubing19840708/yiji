/**
 * 文件上传（唯一出口）：wx.uploadFile 到 /uploads，返回后端可访问 URL。
 * 页面一律 import utils/upload，不要直接调 wx.uploadFile。
 */
import { BASE_URL } from './config';
import { getToken } from './auth';
import { logger } from './logger';

const ALLOWED = ['jpg', 'jpeg', 'png', 'gif', 'webp'];

export function uploadImage(filePath: string): Promise<string> {
  return ensureUnderLimit(filePath).then((path) => doUpload(path));
}

/** 超过 4MB（后端上限 5MB，留余量）自动压缩一档；压缩失败仍用原图交由后端校验报错 */
function ensureUnderLimit(filePath: string): Promise<string> {
  return new Promise<string>((resolve) => {
    wx.getFileSystemManager().getFileInfo({
      filePath,
      success(info) {
        if (info.size <= 4 * 1024 * 1024) {
          resolve(filePath);
          return;
        }
        wx.compressImage({
          src: filePath,
          quality: 60,
          success(c) {
            logger.info('图片上传', `原图 ${(info.size / 1048576).toFixed(1)}MB 超限，已压缩`);
            resolve(c.tempFilePath);
          },
          fail() {
            resolve(filePath);
          }
        });
      },
      fail() {
        resolve(filePath); // 取不到大小（如 devtools 某些临时路径）直接上传
      }
    });
  });
}

function doUpload(filePath: string): Promise<string> {
  const ext = (filePath.split('.').pop() || '').toLowerCase();
  if (ALLOWED.indexOf(ext) < 0) {
    logger.warn('图片上传', `不支持的格式：${filePath}（仅支持 ${ALLOWED.join('/')}）`);
    return Promise.reject(new Error('仅支持图片格式：' + ALLOWED.join('/')));
  }
  return new Promise<string>((resolve, reject) => {
    wx.uploadFile({
      url: `${BASE_URL}/uploads`,
      filePath,
      name: 'file',
      header: { Authorization: `Bearer ${getToken()}` },
      timeout: 15000,
      success(res) {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          // 真机排查：状态码 + 响应体截断入日志（上传通道不经过 request 层，需单独上报）
          logger.warn('图片上传', `POST /uploads ${res.statusCode} ${String(res.data || '').slice(0, 200)}`);
          reject(new Error(`上传失败（${res.statusCode}）`));
          return;
        }
        try {
          const data = JSON.parse(res.data) as { url?: string };
          if (data.url) {
            logger.info('图片上传', `成功 ${data.url.slice(-48)}`);
            resolve(data.url);
          } else {
            logger.warn('图片上传', `响应缺少 url：${String(res.data).slice(0, 200)}`);
            reject(new Error('上传响应缺少 url'));
          }
        } catch (e) {
          logger.warn('图片上传', `响应解析失败：${String(res.data).slice(0, 200)}`);
          reject(new Error('上传响应解析失败'));
        }
      },
      fail(err) {
        // 真机排查：记录 errMsg（域名不通/超时/文件过大等都在这里）
        logger.warn('图片上传', `网络失败：${(err && err.errMsg) || 'unknown'}`);
        reject(new Error('网络异常，上传失败'));
      }
    });
  });
}

/** 拍照/相册选图（单张，压缩），成功返回临时路径可直接传 uploadImage */
export function chooseImage(): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sizeType: ['compressed'],
      success(res) {
        if (res.tempFiles && res.tempFiles.length) {
          resolve(res.tempFiles[0].tempFilePath);
        } else {
          reject(new Error('未选择图片'));
        }
      },
      fail() {
        reject(new Error('未选择图片'));
      }
    });
  });
}

// ==================== 视频（款式产品视频，F1/B1 契约：POST /uploads/video，上限 100MB） ====================

const VIDEO_MAX = 100 * 1024 * 1024;
// 与后端 VIDEO_ALLOWED_EXT 一致：小程序 <video> 可解码的 ftyp 家族
const VIDEO_ALLOWED_EXT = ['mp4', 'm4v', 'mov', '3gp'];

/** 选视频（wx.chooseMedia video），成功返回临时路径可直接传 uploadVideo */
export function chooseVideo(): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    wx.chooseMedia({
      count: 1,
      mediaType: ['video'],
      maxDuration: 60,
      success(res) {
        if (res.tempFiles && res.tempFiles.length) {
          resolve(res.tempFiles[0].tempFilePath);
        } else {
          reject(new Error('未选择视频'));
        }
      },
      fail() {
        reject(new Error('未选择视频'));
      }
    });
  });
}

/**
 * 视频上传（唯一出口）：wx.uploadFile 到 /uploads/video，form-data field 同图片（name='file'）。
 * 前端预校验：mp4/m4v/mov/3gp、上限 100MB（后端同规，提前拦截省流量）。
 */
export function uploadVideo(filePath: string): Promise<string> {
  const ext = (filePath.split('.').pop() || '').toLowerCase();
  if (VIDEO_ALLOWED_EXT.indexOf(ext) < 0) {
    return Promise.reject(new Error('仅支持 mp4/m4v/mov/3gp 视频'));
  }
  return new Promise<string>((resolve, reject) => {
    wx.getFileSystemManager().getFileInfo({
      filePath,
      success(info) {
        if (info.size > VIDEO_MAX) {
          reject(new Error(`视频超过 100MB（当前 ${(info.size / 1048576).toFixed(0)}MB）`));
          return;
        }
        doUploadVideo(filePath).then(resolve).catch(reject);
      },
      fail() {
        // 取不到大小（devtools 某些临时路径）：直接上传，交由后端校验
        doUploadVideo(filePath).then(resolve).catch(reject);
      }
    });
  });
}

function doUploadVideo(filePath: string): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    wx.uploadFile({
      url: `${BASE_URL}/uploads/video`,
      filePath,
      name: 'file',
      header: { Authorization: `Bearer ${getToken()}` },
      timeout: 120000, // 视频大：放宽超时（图片 15s 不够用）
      success(res) {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          logger.warn('视频上传', `POST /uploads/video ${res.statusCode} ${String(res.data || '').slice(0, 200)}`);
          reject(new Error(`上传失败（${res.statusCode}）`));
          return;
        }
        try {
          const data = JSON.parse(res.data) as { url?: string };
          if (data.url) {
            logger.info('视频上传', `成功 ${data.url.slice(-48)}`);
            resolve(data.url);
          } else {
            logger.warn('视频上传', `响应缺少 url：${String(res.data).slice(0, 200)}`);
            reject(new Error('上传响应缺少 url'));
          }
        } catch (e) {
          logger.warn('视频上传', `响应解析失败：${String(res.data).slice(0, 200)}`);
          reject(new Error('上传响应解析失败'));
        }
      },
      fail(err) {
        logger.warn('视频上传', `网络失败：${(err && err.errMsg) || 'unknown'}`);
        reject(new Error('网络异常，上传失败'));
      }
    });
  });
}
