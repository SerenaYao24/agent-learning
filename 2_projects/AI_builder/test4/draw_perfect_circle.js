// Pi Day Challenge - inject perfect circle points directly
async page => {
  const result = await page.evaluate(() => {
    const canvas = document.getElementById('drawingCanvas');
    const W = canvas.width, H = canvas.height;
    const cx = W / 2, cy = H / 2;
    const r = Math.min(W, H) * 0.42;
    const N = 50000;

    // 在 canvas 上画一个完美的圆
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, W, H);
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();

    // 直接设置 points 为完美圆形点（浮点精度）
    points.length = 0;
    for (let i = 0; i <= N; i++) {
      const a = (i / N) * Math.PI * 2;
      points.push({ x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) });
    }

    // 调用计算
    calculatePi();

    return document.getElementById('result').innerHTML;
  });

  console.log('Result:', result);
  return result;
}
