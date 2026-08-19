import { describe, expect, it } from 'vitest';
import {
  displayGeneratedFileName,
  isTechnicalGeneratedFile,
  visibleGeneratedFiles,
} from './generatedFiles';

describe('generatedFiles', () => {
  it('hides metadata json from the main card list', () => {
    const files = [
      { name: 'image_00027_.png', path: '/artifacts/uuid/image_00027_.png' },
      { name: '93c25bef-4665-487c-86da-6f0887263e6f.metadata.json', path: '/artifacts/uuid/x.metadata.json' },
    ];
    expect(files.filter(isTechnicalGeneratedFile)).toHaveLength(1);
    expect(visibleGeneratedFiles(files).map((file) => file.name)).toEqual(['image_00027_.png']);
  });

  it('strips uuid prefixes from display names', () => {
    expect(displayGeneratedFileName({
      name: '93c25bef-4665-487c-86da-6f0887263e6f_cover.png',
      path: '/tmp/93c25bef-4665-487c-86da-6f0887263e6f_cover.png',
    })).toBe('cover.png');
  });
});
