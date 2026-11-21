/*
 * tj_min.h -- the few libturbojpeg calls pod-view uses. Owner: agent-1.
 * The Pi has /usr/lib/libturbojpeg.so.0 but no turbojpeg.h (neither does the SDK), so these
 * declarations are copied from the stable public TurboJPEG 1.x/2.x C API (symbols checked
 * with nm on the Pi: tjInitCompress, tjCompress2, tjFree, tjDestroy, tjGetErrorStr).
 */
#ifndef POD_TJ_MIN_H
#define POD_TJ_MIN_H

#ifdef __cplusplus
extern "C" {
#endif

typedef void* tjhandle;

#define TJPF_RGB        0      /* pixel format: R,G,B bytes */
#define TJSAMP_420      2      /* 4:2:0 chroma subsampling */
#define TJFLAG_FASTDCT  2048   /* faster, slightly less accurate DCT */

tjhandle tjInitCompress(void);
int tjCompress2(tjhandle handle, const unsigned char* srcBuf, int width, int pitch, int height,
                int pixelFormat, unsigned char** jpegBuf, unsigned long* jpegSize,
                int jpegSubsamp, int jpegQual, int flags);
void tjFree(unsigned char* buffer);
int tjDestroy(tjhandle handle);
char* tjGetErrorStr(void);

#ifdef __cplusplus
}
#endif

#endif /* POD_TJ_MIN_H */
