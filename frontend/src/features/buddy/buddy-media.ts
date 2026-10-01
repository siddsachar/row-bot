/** Remove a nearly uniform edge-connected background, as in the NiceGUI Buddy canvas. */
export function keyBuddyBackground(frame: ImageData): ImageData {
  const { data, width, height } = frame;
  if (!width || !height) return frame;
  const corners = [
    [0, 0],
    [width - 1, 0],
    [0, height - 1],
    [width - 1, height - 1],
  ];
  const background = [0, 1, 2].map(
    (channel) =>
      corners.reduce(
        (sum, [x, y]) => sum + data[(y * width + x) * 4 + channel],
        0,
      ) / 4,
  );
  const luma = (red: number, green: number, blue: number) =>
    red * 0.2126 + green * 0.7152 + blue * 0.0722;
  const backgroundLuma = luma(background[0], background[1], background[2]);
  const count = width * height;
  const visited = new Uint8Array(count);
  const queue = new Int32Array(count);
  let head = 0;
  let tail = 0;
  const enqueue = (pixel: number) => {
    if (visited[pixel]) return;
    visited[pixel] = 1;
    const index = pixel * 4;
    if (!data[index + 3]) return;
    const red = data[index];
    const green = data[index + 1];
    const blue = data[index + 2];
    const distance = Math.hypot(
      red - background[0],
      green - background[1],
      blue - background[2],
    );
    if (
      distance >= 24 ||
      Math.abs(luma(red, green, blue) - backgroundLuma) >= 15
    )
      return;
    queue[tail++] = pixel;
  };
  for (let x = 0; x < width; x++) {
    enqueue(x);
    enqueue((height - 1) * width + x);
  }
  for (let y = 0; y < height; y++) {
    enqueue(y * width);
    enqueue(y * width + width - 1);
  }
  while (head < tail) {
    const pixel = queue[head++];
    data[pixel * 4 + 3] = 0;
    const x = pixel % width;
    if (x) enqueue(pixel - 1);
    if (x < width - 1) enqueue(pixel + 1);
    if (pixel >= width) enqueue(pixel - width);
    if (pixel < count - width) enqueue(pixel + width);
  }
  return frame;
}

export function drawBuddyMedia(
  target: HTMLCanvasElement,
  source: HTMLImageElement | HTMLVideoElement,
  scratch: HTMLCanvasElement,
): boolean {
  const width = 132;
  if (target.width !== width || target.height !== width) {
    target.width = width;
    target.height = width;
  }
  if (scratch.width !== width || scratch.height !== width) {
    scratch.width = width;
    scratch.height = width;
  }
  const sourceWidth =
    source instanceof HTMLVideoElement
      ? source.videoWidth
      : source.naturalWidth;
  const sourceHeight =
    source instanceof HTMLVideoElement
      ? source.videoHeight
      : source.naturalHeight;
  if (!sourceWidth || !sourceHeight) return false;
  const scratchContext = scratch.getContext('2d', { willReadFrequently: true });
  const context = target.getContext('2d');
  if (!scratchContext || !context) return false;
  try {
    const side = Math.min(sourceWidth, sourceHeight);
    scratchContext.clearRect(0, 0, width, width);
    scratchContext.drawImage(
      source,
      (sourceWidth - side) / 2,
      (sourceHeight - side) / 2,
      side,
      side,
      0,
      0,
      width,
      width,
    );
    const frame = scratchContext.getImageData(0, 0, width, width);
    keyBuddyBackground(frame);
    context.clearRect(0, 0, width, width);
    context.putImageData(frame, 0, 0);
    return true;
  } catch {
    return false;
  }
}
