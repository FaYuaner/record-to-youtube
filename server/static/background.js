'use strict';

// All segmentation assets and execution stay on this origin and this device.
window.RecorderBackground = class RecorderBackground {
  constructor(canvas, onFailure) {
    this.canvas = canvas; this.onFailure = onFailure; this.segmenter = null;
    this.person = document.createElement('canvas'); this.mask = document.createElement('canvas');
    this.mode = 'off'; this.image = null; this.active = false; this.generation = 0;
  }
  async prepare() {
    if (this.segmenter) return;
    if (!this.loading) this.loading = (async () => {
      const { FilesetResolver, ImageSegmenter } = await import('./vendor/vision/vision_bundle.mjs');
      const files = await FilesetResolver.forVisionTasks('./vendor/vision/wasm');
      const options = { baseOptions: { modelAssetPath: './vendor/vision/selfie_segmenter_landscape.tflite', delegate: 'GPU' },
        runningMode: 'VIDEO', outputCategoryMask: false, outputConfidenceMasks: true };
      try { this.segmenter = await ImageSegmenter.createFromOptions(files, options); }
      catch (_) { options.baseOptions.delegate = 'CPU'; this.segmenter = await ImageSegmenter.createFromOptions(files, options); }
    })().catch((error) => { this.loading = null; throw error; });
    return this.loading;
  }
  async setImage(blob) {
    if (!blob || !['image/jpeg', 'image/png', 'image/webp'].includes(blob.type) || blob.size > 10 * 1024 ** 2)
      throw new Error('請選擇 10 MB 以內的 JPG、PNG 或 WebP 圖片。');
    const bitmap = await createImageBitmap(blob);
    if (bitmap.width * bitmap.height > 16000000) { bitmap.close(); throw new Error('圖片尺寸過大，請選擇較小的圖片。'); }
    this.image?.close(); this.image = bitmap;
  }
  paintBackground(ctx, w, h) {
    if (this.mode === 'custom') {
      if (!this.image) throw new Error('請先選擇虛擬背景圖片。');
      const scale = Math.max(w / this.image.width, h / this.image.height);
      ctx.drawImage(this.image, (w - this.image.width * scale) / 2, (h - this.image.height * scale) / 2,
        this.image.width * scale, this.image.height * scale);
    } else {
      const colors = this.mode === 'blue' ? ['#bdcdd8', '#7c95a9'] : ['#e8dfd0', '#bfb39f'];
      const gradient = ctx.createLinearGradient(0, 0, w, h); gradient.addColorStop(0, colors[0]); gradient.addColorStop(1, colors[1]);
      ctx.fillStyle = gradient; ctx.fillRect(0, 0, w, h);
    }
  }
  draw(video, result) {
    const confidence = result.confidenceMasks?.[0];
    if (!confidence) throw new Error('未能分離人物和背景。');
    if (this.mask.width !== confidence.width || this.mask.height !== confidence.height) {
      this.mask.width = confidence.width; this.mask.height = confidence.height;
      this.maskPixels = this.mask.getContext('2d').createImageData(confidence.width, confidence.height);
    }
    const scores = confidence.getAsFloat32Array(), pixels = this.maskPixels.data;
    for (let i = 0; i < scores.length; i++) {
      const alpha = Math.max(0, Math.min(1, (scores[i] - .2) / .6));
      pixels[i * 4] = pixels[i * 4 + 1] = pixels[i * 4 + 2] = 255;
      pixels[i * 4 + 3] = Math.round(alpha * 255);
    }
    this.mask.getContext('2d').putImageData(this.maskPixels, 0, 0);
    const w = this.canvas.width, h = this.canvas.height, person = this.person.getContext('2d');
    person.globalCompositeOperation = 'copy'; person.drawImage(video, 0, 0, w, h);
    person.globalCompositeOperation = 'destination-in'; person.drawImage(this.mask, 0, 0, w, h);
    person.globalCompositeOperation = 'source-over';
    const output = this.canvas.getContext('2d', { alpha: false });
    this.paintBackground(output, w, h); output.drawImage(this.person, 0, 0);
  }
  async start(video, rawStream, mode) {
    this.stop(); this.mode = mode;
    if (mode === 'off') return rawStream;
    if (mode === 'custom' && !this.image) throw new Error('請先選擇虛擬背景圖片。');
    await this.prepare();
    const generation = ++this.generation;
    this.canvas.width = this.person.width = video.videoWidth;
    this.canvas.height = this.person.height = video.videoHeight;
    this.video = video; this.active = true; let previousTime = -1;
    const render = () => {
      if (!this.active || generation !== this.generation) return;
      try {
        if (video.currentTime !== previousTime) {
          previousTime = video.currentTime;
          this.segmenter.segmentForVideo(video, performance.now(), (result) => this.draw(video, result));
        }
        if (video.requestVideoFrameCallback) this.callback = video.requestVideoFrameCallback(render);
        else this.callback = requestAnimationFrame(render);
      } catch (_) {
        this.active = false;
        this.onFailure('虛擬背景處理中斷，正在保存已有錄影。請重試或改用原背景。');
      }
    };
    render();
    if (!this.active) throw new Error('虛擬背景無法啟用，請重試或改用原背景。');
    this.canvas.hidden = false;
    this.outputStream = this.canvas.captureStream(30);
    for (const track of rawStream.getAudioTracks()) this.outputStream.addTrack(track);
    return this.outputStream;
  }
  stop() {
    this.active = false; this.generation++;
    if (this.video?.cancelVideoFrameCallback) this.video.cancelVideoFrameCallback(this.callback);
    else cancelAnimationFrame(this.callback);
    this.outputStream?.getVideoTracks().forEach((track) => track.stop());
    this.outputStream = null; this.canvas.hidden = true;
  }
};
