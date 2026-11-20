#include "canvas.h"
#include <QMouseEvent>
#include <QPainter>
#include <QResizeEvent>
#include <QPen>
#include <QWheelEvent>
#include <algorithm>
#include <cmath>
#include <limits>

namespace {
bool segmentIntersectsRect(double x1, double y1, double x2, double y2,
                           double left, double top, double right, double bottom) {
    double t0 = 0.0;
    double t1 = 1.0;
    const double dx = x2 - x1;
    const double dy = y2 - y1;
    const double p[] = {-dx, dx, -dy, dy};
    const double q[] = {x1 - left, right - x1, y1 - top, bottom - y1};
    for (int i = 0; i < 4; ++i) {
        if (std::abs(p[i]) < 1e-15) {
            if (q[i] < 0.0) return false;
            continue;
        }
        const double r = q[i] / p[i];
        if (p[i] < 0.0) t0 = std::max(t0, r);
        else t1 = std::min(t1, r);
        if (t0 > t1) return false;
    }
    return true;
}
}

Canvas::Canvas(QWidget *parent) : QWidget(parent) {
    setMinimumSize(300, 300);
    setAutoFillBackground(false);
}
void Canvas::setLayers(const std::vector<Layer> *layers) { layers_ = layers; update(); }
void Canvas::addLayer(const Layer *layer) {
    if (!layer) return;
    const bool empty = !hasView_;
    double left = empty ? std::numeric_limits<double>::infinity() : worldView_.left();
    double right = empty ? -std::numeric_limits<double>::infinity() : worldView_.right();
    double top = empty ? std::numeric_limits<double>::infinity() : worldView_.top();
    double bottom = empty ? -std::numeric_limits<double>::infinity() : worldView_.bottom();
    for (const auto &stroke : layer->strokes) {
        const double radius = stroke.diameter / 2.0;
        left = std::min(left, std::min(stroke.x1, stroke.x2) - radius);
        right = std::max(right, std::max(stroke.x1, stroke.x2) + radius);
        top = std::min(top, std::min(stroke.y1, stroke.y2) - radius);
        bottom = std::max(bottom, std::max(stroke.y1, stroke.y2) + radius);
    }
    if (!std::isfinite(left)) return;
    worldView_ = QRectF(QPointF(left, top), QPointF(right, bottom));
    hasView_ = true;
    yMax_ = std::max(yMax_, layer->strokes.empty() ? 0.0 : [&] {
        double value = layer->strokes.front().y1;
        for (const auto &s : layer->strokes) value = std::max(value, std::max(s.y1, s.y2) + s.diameter / 2.0);
        return value;
    }());
    if (width() <= 0 || height() <= 0) {
        fitOnResize_ = true;
        return;
    }
    fitOnResize_ = false;
    fit();
}
void Canvas::resetLayers() {
    worldView_ = QRectF();
    hasView_ = false;
    yMax_ = 0.0;
    center_ = QPointF();
    pan_ = QPointF();
    scale_ = 1.0;
    fitOnResize_ = false;
    update();
}

void Canvas::fit() {
    double x0 = std::numeric_limits<double>::infinity(), y0 = x0;
    double x1 = -x0, y1 = -x0;
    if (layers_) for (const auto &layer : *layers_) if (layer.visible) {
        for (const auto &s : layer.strokes) {
            const double r = s.diameter / 2.0;
            x0 = std::min(x0, std::min(s.x1, s.x2) - r);
            y0 = std::min(y0, std::min(s.y1, s.y2) - r);
            x1 = std::max(x1, std::max(s.x1, s.x2) + r);
            y1 = std::max(y1, std::max(s.y1, s.y2) + r);
        }
    }
    if (!std::isfinite(x0)) {
        fitOnResize_ = true;
        return;
    }
    fitOnResize_ = false;
    const bool firstFit = !hasView_;
    worldView_ = QRectF(QPointF(x0, y0), QPointF(x1, y1));
    hasView_ = true;
    if (width() <= 0 || height() <= 0) {
        fitOnResize_ = true;
        return;
    }
    center_ = worldView_.center();
    scale_ = std::clamp(std::min(width() / std::max(x1 - x0, 0.001),
                                 height() / std::max(y1 - y0, 0.001)) * 0.9, 0.001, 100000.0);
    if (firstFit) pan_ = QPointF();
    updateWorldView();
    update();
}
void Canvas::paintEvent(QPaintEvent *) {
    QPainter p(this);
    p.fillRect(rect(), QColor("#0f1115"));
    p.setRenderHint(QPainter::Antialiasing);
    if (!layers_) return;
    p.translate(QPointF(width() / 2.0, height() / 2.0) + pan_);
    p.scale(scale_, -scale_);
    p.translate(-center_);
    const QRectF view = p.combinedTransform().inverted().mapRect(QRectF(rect())).adjusted(-2 / scale_, -2 / scale_, 2 / scale_, 2 / scale_);
    const double left = view.left();
    const double right = view.right();
    const double top = view.top();
    const double bottom = view.bottom();
    for (const auto &layer : *layers_) if (layer.visible) {
        for (const auto &s : layer.strokes) {
            const double radius = s.diameter / 2.0;
            if (std::max(s.x1, s.x2) + radius < left || std::min(s.x1, s.x2) - radius > right
                || std::max(s.y1, s.y2) + radius < top || std::min(s.y1, s.y2) - radius > bottom) continue;
            QPen pen(layer.color, s.diameter, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin);
            p.setPen(pen);
            if (s.flash) {
                p.setPen(Qt::NoPen);
                p.setBrush(layer.color);
                p.drawEllipse(QPointF(s.x1, s.y1), s.diameter / 2.0, s.diameter / 2.0);
            } else {
                if (!segmentIntersectsRect(s.x1, s.y1, s.x2, s.y2,
                                           left - radius, top - radius,
                                           right + radius, bottom + radius)) continue;
                p.setBrush(Qt::NoBrush);
                p.drawLine(QPointF(s.x1, s.y1), QPointF(s.x2, s.y2));
            }
        }
    }
}
void Canvas::wheelEvent(QWheelEvent *event) {
    const double next = std::clamp(scale_ * std::pow(1.0015, event->angleDelta().y()), 0.001, 100000.0);
    const QPointF cursor = event->position() - QPointF(width() / 2.0, height() / 2.0);
    pan_ = cursor - (cursor - pan_) * (next / scale_);
    scale_ = next;
    QTransform transform;
    transform.translate(width() / 2.0 + pan_.x(), height() / 2.0 + pan_.y());
    transform.scale(scale_, -scale_);
    transform.translate(-center_.x(), -center_.y());
    updateWorldView();
    update();
    event->accept();
}
void Canvas::mousePressEvent(QMouseEvent *event) {
    if (event->button() == Qt::RightButton) { dragging_ = true; last_ = event->pos(); event->accept(); }
}
void Canvas::mouseMoveEvent(QMouseEvent *event) {
    if (dragging_) { pan_ += event->pos() - last_; last_ = event->pos(); updateWorldView(); update(); }
}
void Canvas::mouseReleaseEvent(QMouseEvent *event) {
    if (event->button() == Qt::RightButton) { dragging_ = false; event->accept(); }
}
void Canvas::resizeEvent(QResizeEvent *event) {
    QWidget::resizeEvent(event);
    if (fitOnResize_) {
        fit();
    } else updateWorldView();
}

void Canvas::updateWorldView() {
    if (width() <= 0 || height() <= 0 || scale_ <= 0.0) return;
    QTransform transform;
    transform.translate(width() / 2.0 + pan_.x(), height() / 2.0 + pan_.y());
    transform.scale(scale_, -scale_);
    transform.translate(-center_.x(), -center_.y());
    const QRectF view = transform.inverted().mapRect(QRectF(rect()));
    worldView_ = view;
}
