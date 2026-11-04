// Rounded host-backdrop brush, following ChatGPTnx's composition geometry.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <DispatcherQueue.h>
#include <windows.ui.composition.interop.h>
#include <winrt/Windows.Foundation.Numerics.h>
#include <winrt/Windows.System.h>
#include <winrt/Windows.UI.Composition.h>
#include <winrt/Windows.UI.Composition.Desktop.h>
#include <algorithm>

using namespace winrt;
using namespace Windows::UI::Composition;
using namespace Windows::UI::Composition::Desktop;

namespace {
struct ThreadComposition {
    Windows::System::DispatcherQueueController queue{nullptr};
    Compositor compositor{nullptr};
    ThreadComposition() {
        DispatcherQueueOptions options{sizeof(DispatcherQueueOptions),DQTYPE_THREAD_CURRENT,DQTAT_COM_NONE};
        if (!Windows::System::DispatcherQueue::GetForCurrentThread())
            check_hresult(CreateDispatcherQueueController(options,
                reinterpret_cast<PDISPATCHERQUEUECONTROLLER*>(put_abi(queue))));
        compositor=Compositor();
    }
};
Compositor compositor() {
    thread_local ThreadComposition context;
    return context.compositor;
}
struct Backdrop {
    DesktopWindowTarget target{nullptr};
    SpriteVisual visual{nullptr};
    CompositionRoundedRectangleGeometry geometry{nullptr};
    CompositionGeometricClip clip{nullptr};
    CompositionBackdropBrush brush{nullptr};
    explicit Backdrop(HWND hwnd) {
        auto c=compositor();
        check_hresult(c.as<ABI::Windows::UI::Composition::Desktop::ICompositorDesktopInterop>()->CreateDesktopWindowTarget(hwnd,FALSE,
            reinterpret_cast<ABI::Windows::UI::Composition::Desktop::IDesktopWindowTarget**>(put_abi(target))));
        if (!target)throw hresult_error(E_FAIL);
        brush=c.CreateHostBackdropBrush();
        geometry=c.CreateRoundedRectangleGeometry();
        clip=c.CreateGeometricClip(geometry);
        visual=c.CreateSpriteVisual();visual.Brush(brush);visual.Clip(clip);
    }
    ~Backdrop() noexcept {
        try {target.Root(nullptr);} catch (...) {}
    }
};
}
extern "C" {
__declspec(dllexport) unsigned __stdcall RemoteBackdropApi() noexcept {return 1;}
__declspec(dllexport) HRESULT __stdcall RemoteBackdropCreate(HWND hwnd,void** result) noexcept {
    if (!result)return E_POINTER;
    *result=nullptr;
    if (!hwnd||!IsWindow(hwnd))return E_INVALIDARG;
    try {*result=new Backdrop(hwnd);return S_OK;}catch (...){return to_hresult();}
}
__declspec(dllexport) HRESULT __stdcall RemoteBackdropResize(void* handle,int width,int height,int radius) noexcept {
    if (!handle||width<1||height<1||radius<1)return E_INVALIDARG;
    try {
        auto b=static_cast<Backdrop*>(handle);
        radius=std::min(radius,std::min(width,height)/2);
        Windows::Foundation::Numerics::float2 size{float(width),float(height)};
        b->visual.Size(size);b->geometry.Size(size);
        b->geometry.CornerRadius({float(radius),float(radius)});
        return S_OK;
    }catch (...){return to_hresult();}
}
__declspec(dllexport) HRESULT __stdcall RemoteBackdropVisible(void* handle,BOOL visible) noexcept {
    if (!handle)return E_INVALIDARG;
    try {auto b=static_cast<Backdrop*>(handle);b->target.Root(visible?b->visual:nullptr);return S_OK;}
    catch (...){return to_hresult();}
}
__declspec(dllexport) void __stdcall RemoteBackdropDestroy(void* handle) noexcept {delete static_cast<Backdrop*>(handle);}
}
