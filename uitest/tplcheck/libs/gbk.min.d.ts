/** gbk.js 0.3.0（UMD/CommonJS）类型声明：https://github.com/cnwhy/GBK.js */
declare const GBK: {
  encode(str: string): Uint8Array;
  decode(bytes: Uint8Array | number[] | ArrayBuffer): string;
  URI(str: string): string;
};
export = GBK;
