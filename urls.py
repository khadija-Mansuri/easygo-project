# from django.urls import path,include
# from Tours import views
#
# urlpatterns = [
#     path('', include('Tours.urls')),
#     path('', views.home, name='home'),
#     path('about/', views.about, name='about'),
#     path('services/', views.services, name='services'),
#     path('packages/', views.packages, name='packages'),
#     path('blog/', views.blog, name='blog'),
#     path('destination/', views.destination, name='destination'),
#     path('tour/', views.tour, name='tour'),
#     path('booking/', views.booking, name='booking'),
#     path('gallery/', views.gallery, name='gallery'),
#     path('guides/', views.guides, name='guides'),
#     path('testimonial/', views.testimonial, name='testimonial'),
#     path('contact/', views.contact, name='contact'),
#     path('subscribe/', views.subscribe, name='subscribe'),
#     path('404/', views.error404, name='error404'),
#
#
#
# ]
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
# Import the sitemap classes from your Tours app
from Tours.sitemaps import PackageSitemap, StaticViewSitemap
from django.contrib.sitemaps.views import sitemap
from django.views.generic.base import RedirectView

sitemaps = {
    'packages': PackageSitemap,
    'static': StaticViewSitemap,
}
urlpatterns = [
    path('favicon.ico', RedirectView.as_view(url=settings.STATIC_URL + 'img/favicon.ico')),
    path('admin/', admin.site.urls),
    path('', include('Tours.urls')),
# The Sitemap URL
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps},
         name='django.contrib.sitemaps.views.sitemap'),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)