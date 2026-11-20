#include "canvas.h"
#include "viewer_core.h"
#include <QApplication>
#include <QByteArray>
#include <QColorDialog>
#include <QCoreApplication>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFileDevice>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QLabel>
#include <QMainWindow>
#include <QMessageBox>
#include <QPushButton>
#include <QStatusBar>
#include <QStandardItemModel>
#include <QTextStream>
#include <QTreeView>
#include <QTimer>
#include <QHeaderView>
#include <QVBoxLayout>
#include <QWidget>
#include <memory>
#include <utility>
#include <vector>

class MainWindow final : public QMainWindow {
public:
    MainWindow() {
        setWindowTitle(QStringLiteral("Evan Gerber · Qt/Rust 迁移原型"));
        resize(1100, 720);
        auto *compare = new QGroupBox(QStringLiteral("A/B 对比（尚未迁移；请继续使用旧版查看器）"), this);
        compare->setEnabled(false);
        auto *compareLayout = new QHBoxLayout(compare);
        compareLayout->addWidget(new QPushButton(QStringLiteral("A组导入"), compare));
        compareLayout->addWidget(new QPushButton(QStringLiteral("B组导入"), compare));
        compareLayout->addWidget(new QPushButton(QStringLiteral("自动定位差异"), compare));
        auto *central = new QWidget(this);
        auto *layout = new QVBoxLayout(central);
        auto *warning = new QLabel(QStringLiteral("实验原型：仅支持无孔圆形光圈、直线和焊盘；复杂 Gerber 和钻孔请继续使用旧版，不用于制造数据确认。"));
        warning->setWordWrap(true);
        layout->addWidget(warning);
        layout->addWidget(compare);
        auto *bar = new QHBoxLayout;
        auto *open = new QPushButton(QStringLiteral("导入文件"));
        auto *fit = new QPushButton(QStringLiteral("适应窗口"));
        auto *clear = new QPushButton(QStringLiteral("清空"));
        bar->addWidget(open); bar->addWidget(fit); bar->addWidget(clear); bar->addStretch();
        layout->addLayout(bar);
        auto *body = new QHBoxLayout;
        list_ = new QTreeView;
        model_ = new QStandardItemModel(list_);
        model_->setHorizontalHeaderLabels({QStringLiteral("颜色"), QStringLiteral("显示"), QStringLiteral("图层")});
        list_->setModel(model_);
        list_->setColumnWidth(0, 50);
        list_->setColumnWidth(1, 55);
        list_->setMinimumWidth(270);
        list_->setAlternatingRowColors(true);
        list_->setRootIsDecorated(false);
        list_->header()->setStretchLastSection(true);
        list_->setEditTriggers(QAbstractItemView::NoEditTriggers);
        canvas_ = new Canvas;
        canvas_->setLayers(&layers_);
        body->addWidget(list_); body->addWidget(canvas_, 1);
        layout->addLayout(body, 1);
        setCentralWidget(central);
        statusBar()->showMessage(QStringLiteral("勾选图层显示；单击颜色色块改色；滚轮缩放，右键拖动平移"));
        connect(open, &QPushButton::clicked, this, [this] { openFiles(); });
        connect(fit, &QPushButton::clicked, canvas_, &Canvas::fit);
        connect(clear, &QPushButton::clicked, this, [this] {
            layers_.clear();
            model_->removeRows(0, model_->rowCount());
            canvas_->resetLayers();
        });
        connect(model_, &QStandardItemModel::itemChanged, this, [this](QStandardItem *item) {
            const int row = item->row();
            if (row < 0 || static_cast<size_t>(row) >= layers_.size()) return;
            if (item->column() == 1) {
                layers_[static_cast<size_t>(row)].visible = item->checkState() == Qt::Checked;
                canvas_->fit();
                canvas_->update();
            }
        });
        connect(list_, &QTreeView::clicked, this, [this](const QModelIndex &index) {
            if (!index.isValid() || index.column() != 0) return;
            const int row = index.row();
            if (row < 0 || static_cast<size_t>(row) >= layers_.size()) return;
            auto *item = model_->item(row, 0);
            const QColor selected = QColorDialog::getColor(layers_[static_cast<size_t>(row)].color,
                                                           this, QStringLiteral("选择图层颜色"));
            if (!selected.isValid()) return;
            layers_[static_cast<size_t>(row)].color = selected;
            item->setForeground(selected);
            item->setData(selected, Qt::UserRole);
            canvas_->update();
        });
    }
private:
    void openFiles() {
        const QStringList paths = QFileDialog::getOpenFileNames(this, QStringLiteral("导入 Gerber"), {},
                                                               QStringLiteral("所有文件 (*)"));
        QStringList errors;
        for (const auto &path : paths) {
            QFile file(path);
            if (!file.open(QIODevice::ReadOnly)) { errors << QFileInfo(path).fileName() + ": " + file.errorString(); continue; }
            if (file.size() <= 0 || file.size() > 128LL * 1024 * 1024) {
                errors << QFileInfo(path).fileName() + QStringLiteral(": 文件为空或超过 128 MiB"); continue;
            }
            const QByteArray bytes = file.readAll();
            if (file.error() != QFileDevice::NoError) { errors << QFileInfo(path).fileName() + ": " + file.errorString(); continue; }
            if (bytes.isEmpty() || bytes.size() > 128LL * 1024 * 1024) {
                errors << QFileInfo(path).fileName() + QStringLiteral(": 读取文件大小超限"); continue;
            }
            ViewerLayer *raw = nullptr;
            char error[512] = {};
            const int code = viewer_parse_gerber(reinterpret_cast<const uint8_t *>(bytes.constData()),
                                                  static_cast<size_t>(bytes.size()), &raw, error, sizeof(error));
            std::unique_ptr<ViewerLayer, decltype(&viewer_layer_free)> parsed(raw, &viewer_layer_free);
            if (code != 0 || !parsed) {
                errors << QFileInfo(path).fileName() + ": " + QString::fromUtf8(error); continue;
            }
            size_t count = 0;
            const ViewerStroke *strokes = viewer_layer_strokes(parsed.get(), &count);
            if (!strokes || count == 0 || count > 2'000'000) {
                errors << QFileInfo(path).fileName() + QStringLiteral(": 图元数据无效"); continue;
            }
            static const QColor palette[] = {QColor("#e53935"), QColor("#1e88e5"), QColor("#43a047"), QColor("#fb8c00")};
            Layer layer;
            layer.name = QFileInfo(path).fileName();
            layer.color = palette[layers_.size() % 4];
            layer.strokes.assign(strokes, strokes + count);
            layers_.push_back(std::move(layer));
            auto *color = new QStandardItem(QStringLiteral("■"));
            color->setForeground(layers_.back().color);
            color->setData(layers_.back().color, Qt::UserRole);
            color->setEditable(false);
            auto *visible = new QStandardItem;
            visible->setCheckable(true);
            visible->setCheckState(Qt::Checked);
            visible->setEditable(false);
            auto *name = new QStandardItem(layers_.back().name);
            name->setEditable(false);
            model_->appendRow({color, visible, name});
            canvas_->addLayer(&layers_.back());
        }
        if (!errors.isEmpty()) QMessageBox::warning(this, QStringLiteral("未导入的文件"), errors.join('\n'));
    }
    std::vector<Layer> layers_;
    QTreeView *list_ = nullptr;
    QStandardItemModel *model_ = nullptr;
    Canvas *canvas_ = nullptr;
};

int main(int argc, char **argv) {
    // Headless FFI smoke test used by regression scripts and CI; must not create QApplication.
    if (argc >= 2 && QString::fromLocal8Bit(argv[1]) == QStringLiteral("--version")) {
        QTextStream(stdout) << "Evan Gerber Qt/Rust preview 0.1.0 (experimental)\n";
        return 0;
    }
    if (argc >= 2 && QString::fromLocal8Bit(argv[1]) == QStringLiteral("--verify")) {
        const QString reportPath = argc >= 3 ? QString::fromLocal8Bit(argv[2]) : QString();
        QFile report(reportPath);
        if (reportPath.isEmpty() || !report.open(QIODevice::WriteOnly | QIODevice::Truncate)) return 2;
        QTextStream out(&report);
        out << "EVAN_GERBER_QT_RUST_VERIFY_V1\n";
        report.close();
        return 0;
    }
    if (argc >= 2 && QString::fromLocal8Bit(argv[1]) == QStringLiteral("--selftest")) {
        const QString reportPath = qEnvironmentVariable("GERBER_VIEWER_SELFTEST_REPORT");
        const QString listPath = qEnvironmentVariable("GERBER_VIEWER_SELFTEST_LIST");
        const bool allowRejects = qEnvironmentVariableIntValue("GERBER_VIEWER_SELFTEST_ALLOW_REJECTS") != 0;
        QStringList inputFiles;
        if (!listPath.isEmpty()) {
            QFile fileList(listPath);
            if (!fileList.open(QIODevice::ReadOnly | QIODevice::Text)) return 3;
            QTextStream list(&fileList);
            while (!list.atEnd()) {
                const QString path = list.readLine().trimmed();
                if (!path.isEmpty()) inputFiles.append(path);
            }
        } else {
            for (int i = 2; i < argc; ++i) inputFiles.append(QString::fromLocal8Bit(argv[i]));
        }
        QFile logFile(reportPath);
        const bool logToFile = !logFile.fileName().isEmpty();
        if (logToFile && !logFile.open(QIODevice::WriteOnly | QIODevice::Truncate)) return 3;
        QString consoleOutput;
        QTextStream out;
        if (logToFile) out.setDevice(&logFile);
        else out.setString(&consoleOutput);
        bool failed = false;
        for (const QString &path : inputFiles) {
            QFile file(path);
            if (!file.open(QIODevice::ReadOnly) || file.size() <= 0 || file.size() > 128LL * 1024 * 1024) {
                out << "FAIL " << path << ": file cannot be read or is outside size limits\n";
                failed = true;
                continue;
            }
            const QByteArray bytes = file.readAll();
            ViewerLayer *raw = nullptr;
            char error[512] = {};
            const int code = viewer_parse_gerber(reinterpret_cast<const uint8_t *>(bytes.constData()),
                                                  static_cast<size_t>(bytes.size()), &raw, error, sizeof(error));
            std::unique_ptr<ViewerLayer, decltype(&viewer_layer_free)> layer(raw, &viewer_layer_free);
            if (code != 0 || !layer) {
                out << "REJECT " << path << ": " << QString::fromUtf8(error) << '\n';
                if (allowRejects) continue;
                failed = true;
                continue;
            }
            size_t count = 0;
            const ViewerStroke *strokes = viewer_layer_strokes(layer.get(), &count);
            if (!strokes || count == 0 || count > 2'000'000) {
                out << "FAIL " << path << ": invalid geometry returned by Rust core\n";
                failed = true;
                continue;
            }
            double minX = strokes[0].x1, minY = strokes[0].y1;
            double maxX = minX, maxY = minY;
            for (size_t n = 0; n < count; ++n) {
                const auto &s = strokes[n];
                const double r = s.diameter / 2.0;
                minX = std::min(minX, std::min(s.x1, s.x2) - r);
                minY = std::min(minY, std::min(s.y1, s.y2) - r);
                maxX = std::max(maxX, std::max(s.x1, s.x2) + r);
                maxY = std::max(maxY, std::max(s.y1, s.y2) + r);
            }
            out << "PASS " << path << ": strokes=" << static_cast<qulonglong>(count)
                << " bbox_mm=" << minX << ',' << minY << ',' << maxX << ',' << maxY << '\n';
        }
        out.flush();
        if (logToFile) logFile.close();
        else QTextStream(stdout) << consoleOutput;
        return failed ? 2 : 0;
    }
    QApplication app(argc, argv);
    MainWindow window;
    window.show();
    for (int i = 1; i < argc; ++i) {
        const QString argument = QString::fromLocal8Bit(argv[i]);
        const QString prefix = QStringLiteral("--smoke=");
        if (argument.startsWith(prefix)) {
            const QString marker = argument.mid(prefix.size());
            QTimer::singleShot(600, &app, [&app, marker] {
                QFile ready(marker);
                if (ready.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
                    ready.write("QT_WINDOW_READY\n");
                    ready.close();
                }
                app.quit();
            });
            break;
        }
    }
    return app.exec();
}
