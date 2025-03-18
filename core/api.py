from django.http import HttpResponse
from django.template.loader import render_to_string
from django.template import RequestContext
from .models import Car

def car_list_api(request):
    client_id = request.GET.get('client_id')
    print(f"API called with GET: {request.GET}")
    print(f"Extracted client_id: {client_id}")
    if client_id and client_id != 'undefined' and client_id.isdigit():
        all_cars = Car.objects.filter(client_id=client_id)
        print(f"All cars for client {client_id}: {all_cars.count()}")
        unloaded_cars = all_cars.filter(status='UNLOADED')
        print(f"Unloaded cars for client {client_id}: {unloaded_cars.count()}")
        for car in unloaded_cars:
            print(f"Car {car.id}: {car} - Status: {car.status}")
        html = render_to_string('admin/car_options.html', {'cars': unloaded_cars}, request=request)
        print(f"Returning HTML: {html}")
        return HttpResponse(html, content_type='text/html')
    print("No valid client_id, returning empty response")
    return HttpResponse('<span>No cars found</span>', content_type='text/html')