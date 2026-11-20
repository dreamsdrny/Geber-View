#ifndef EVAN_VIEWER_CORE_H
#define EVAN_VIEWER_CORE_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct ViewerLayer ViewerLayer;
typedef struct ViewerStroke {
    double x1, y1, x2, y2, diameter;
    uint32_t flash;
} ViewerStroke;
int32_t viewer_parse_gerber(const uint8_t *data, size_t len, ViewerLayer **output,
                            char *error, size_t error_capacity);
const ViewerStroke *viewer_layer_strokes(const ViewerLayer *layer, size_t *count);
void viewer_layer_free(ViewerLayer *layer);
#ifdef __cplusplus
}
#endif
#endif
