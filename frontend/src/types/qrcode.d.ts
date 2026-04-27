declare module "qrcode" {
  export interface QRCodeToDataURLOptions {
    margin?: number;
    width?: number;
  }

  export function toDataURL(
    text: string,
    options?: QRCodeToDataURLOptions,
  ): Promise<string>;
}
