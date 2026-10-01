import { describe, expect, it } from 'vitest';
import { keyBuddyBackground } from './buddy-media';

describe('Buddy media background', () => {
  it.each([
    [255, 255, 255],
    [9, 22, 27],
  ])(
    'keys edge-connected opaque color without erasing the Buddy center',
    (red, green, blue) => {
      const width = 7;
      const height = 7;
      const data = new Uint8ClampedArray(width * height * 4);
      for (let pixel = 0; pixel < width * height; pixel++) {
        data.set([red, green, blue, 255], pixel * 4);
      }
      for (let y = 2; y <= 4; y++)
        for (let x = 2; x <= 4; x++)
          data.set([180, 100, 20, 255], (y * width + x) * 4);
      const frame = { data, width, height } as ImageData;
      keyBuddyBackground(frame);
      expect(data[3]).toBe(0);
      expect(data[(3 * width + 3) * 4 + 3]).toBe(255);
    },
  );
});
