#ifndef EVAN_CANVAS_H
#define EVAN_CANVAS_H
#include "viewer_core.h"
#include <QColor>
#include <QPoint>
#include <QPointF>
#include <QRectF>
#include <QTransform>
#include <QString>
#include <QWidget>
#include <vector>

struct Layer {
    QString name;
    QColor color;
    bool visible = true;
    std::vector<ViewerStroke> strokes;
};

class Canvas final : public QWidget {
public:
    explicit Canvas(QWidget *parent = nullptr);
    void setLayers(const std::vector<Layer> *layers);
    void addLayer(const Layer *layer);
    void resetLayers();
    void fit();
protected:
    void paintEvent(QPaintEvent *) override;
    void wheelEvent(QWheelEvent *event) override;
    void mousePressEvent(QMouseEvent *event) override;
    void mouseMoveEvent(QMouseEvent *event) override;
    void mouseReleaseEvent(QMouseEvent *event) override;
    void resizeEvent(QResizeEvent *event) override;
private:
    void updateWorldView();
    const std::vector<Layer> *layers_ = nullptr;
    double scale_ = 1.0;
    bool hasView_ = false;
    QPointF center_;
    QPointF pan_;
    QPoint last_;
    bool dragging_ = false;
    bool fitOnResize_ = false;
    QRectF worldView_;
    double yMax_ = 0.0;
};
#endif
