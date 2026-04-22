import google.generativeai as genai
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse, FileResponse
from django.urls import reverse
import random
from django.core.mail import send_mail
from django.contrib import messages
from django.contrib.auth import logout
from django.core.paginator import Paginator
from django.contrib.admin.views.decorators import staff_member_required
from .models import UserRegistration, Package, Booking, PackageDayPlan, Invoice, PassengerDetail, Feedback, ContactMessage
from django.views.decorators.http import require_POST, require_GET
from datetime import datetime
from decimal import Decimal
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from django.db import IntegrityError, transaction
from .forms import UserProfileForm
from django.core.files.storage import FileSystemStorage
import os
import qrcode
import io
import base64
from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from datetime import datetime, timedelta
from django.utils import timezone
import json


def edit_profile(request):
    user_id = request.session.get('user_id')
    if not user_id:
        messages.warning(request, "Please login to edit your profile.")
        return redirect('login')

    user = get_object_or_404(UserRegistration, id=user_id)

    if request.method == "POST":
        form = UserProfileForm(request.POST, request.FILES, instance=user)
        if form.is_valid():
            # Save the form
            updated_user = form.save()

            # Update session data if name or email changed
            request.session['user_name'] = updated_user.name
            request.session['user_email'] = updated_user.email

            messages.success(request, "Profile updated successfully!")
            return redirect('profile')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = UserProfileForm(instance=user)

    return render(request, 'edit_profile.html', {'form': form, 'user': user})
def generate_invoice(booking):
    """Generate invoice for a booking with duplicate handling"""

    # Calculate invoice amounts (existing code remains the same)
    base_price_per_person = booking.package.base_price
    persons = booking.persons
    base_subtotal = base_price_per_person * persons

    # Package type multipliers
    package_multipliers = {
        'standard': 1.0,
        'premium': 1.2,
        'vip': 1.5,
    }
    multiplier = package_multipliers.get(booking.package_type, 1.0)
    package_type_amount = base_subtotal * multiplier

    # Calculate discount based on number of persons
    if persons <= 5:
        discount_percentage = 0
    elif persons <= 10:
        discount_percentage = 5
    elif persons <= 20:
        discount_percentage = 10
    else:
        discount_percentage = 15

    discount_amount = (package_type_amount * discount_percentage) / 100
    after_discount = package_type_amount - discount_amount

    # Tax calculation (5% GST)
    tax_percentage = 5.0
    tax_amount = (after_discount * tax_percentage) / 100
    total_amount = after_discount + tax_amount

    # Calculate refund amount if booking is cancelled
    refund_info = None
    cancellation_charges = 0
    refund_amount = 0

    if booking.booking_status == 'cancelled':
        refund_info = calculate_refund_amount(booking)
        refund_amount = refund_info['refund_amount']
        cancellation_charges = total_amount - refund_amount

    # Get passenger details
    passengers = booking.passengers.all()
    passenger_list = []
    for passenger in passengers:
        passenger_list.append({
            'name': passenger.name,
            'age': passenger.age,
            'gender': passenger.get_gender_display(),
            'aadhar': passenger.aadhar_number if passenger.aadhar_number else 'Not Provided'
        })

    # Create invoice items (detailed breakdown)
    invoice_items = {
        'package_details': {
            'name': booking.package.title,
            'location': booking.package.location,
            'duration': booking.package.duration,
            'category': booking.package.category,
        },
        'booking_details': {
            'travel_mode': booking.get_travel_mode_display(),
            'package_type': booking.get_package_type_display(),
            'persons': persons,
            'travel_date': booking.travel_date,
            'booking_status': booking.booking_status,
        },
        'cost_breakdown': {
            'base_price_per_person': float(base_price_per_person),
            'persons': persons,
            'base_subtotal': float(base_subtotal),
            'package_multiplier': float(multiplier),
            'package_type_amount': float(package_type_amount),
            'discount_percentage': discount_percentage,
            'discount_amount': float(discount_amount),
            'after_discount': float(after_discount),
            'tax_percentage': float(tax_percentage),
            'tax_amount': float(tax_amount),
            'total': float(total_amount)
        },
        'passenger_details': passenger_list,  # Add passenger details here
        'inclusions': [
            'Accommodation as per package',
            'Meals: Breakfast, Lunch, Dinner',
            'Transportation as per itinerary',
            'Sightseeing as per package',
            'Tour Manager services',
            'All permits and entry fees'
        ],
        'exclusions': [
            'Personal expenses',
            'Tips and gratuities',
            'Travel insurance',
            'Any items not mentioned in inclusions'
        ]
    }

    # Add cancellation details if booking is cancelled
    if booking.booking_status == 'cancelled' and refund_info:
        invoice_items['cancellation_details'] = {
            'cancellation_date': timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
            'refund_percentage': refund_info['refund_percentage'],
            'refund_amount': float(refund_amount),
            'cancellation_charges': float(cancellation_charges),
            'refund_message': refund_info['refund_message'],
            'days_until_travel': refund_info['days_until_travel'],
            'hours_until_travel': refund_info['hours_until_travel']
        }

    # Determine payment status based on booking status
    if booking.booking_status == 'cancelled':
        payment_status = 'refunded'
    elif booking.booking_status == 'confirmed':
        payment_status = 'paid'
    else:
        payment_status = 'pending'

    # Use transaction to ensure atomicity
    try:
        with transaction.atomic():
            # Check if invoice already exists
            invoice, created = Invoice.objects.update_or_create(
                booking=booking,
                defaults={
                    'base_amount': base_subtotal,
                    'package_type_multiplier': multiplier,
                    'package_type_amount': package_type_amount,
                    'discount_percentage': discount_percentage,
                    'discount_amount': discount_amount,
                    'tax_percentage': tax_percentage,
                    'tax_amount': tax_amount,
                    'total_amount': total_amount,
                    'invoice_items': invoice_items,
                    'payment_status': payment_status,
                    'payment_method': 'Online Payment'
                }
            )
            return invoice
    except IntegrityError as e:
        # If there's an integrity error (like duplicate invoice number),
        # try one more time with a new invoice number
        if 'UNIQUE constraint failed' in str(e) and 'invoice_number' in str(e):
            # Create new invoice with a different number
            invoice = Invoice(
                booking=booking,
                base_amount=base_subtotal,
                package_type_multiplier=multiplier,
                package_type_amount=package_type_amount,
                discount_percentage=discount_percentage,
                discount_amount=discount_amount,
                tax_percentage=tax_percentage,
                tax_amount=tax_amount,
                total_amount=total_amount,
                invoice_items=invoice_items,
                payment_status=payment_status,
                payment_method='Online Payment'
            )
            # Let the model generate a new unique number
            invoice.save()
            return invoice
        else:
            raise


def create_invoice_pdf(invoice):
    """Generate PDF for invoice"""
    buffer = io.BytesIO()

    # Create the PDF object
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=72, leftMargin=72,
                            topMargin=72, bottomMargin=18)

    # Container for the 'Flowable' objects
    elements = []

    # Styles
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Center', alignment=TA_CENTER))
    styles.add(ParagraphStyle(name='Right', alignment=TA_RIGHT))

    # Company Header
    elements.append(Paragraph("TRAVELA TOURS & TRAVELS", styles['Title']))
    elements.append(Paragraph("123 Travel Street, Tourism Hub, Mumbai - 400001", styles['Normal']))
    elements.append(Paragraph("Phone: +91 98765 43210 | Email: info@travela.com", styles['Normal']))
    elements.append(Paragraph(f"GSTIN: 27ABCDE1234F1Z5", styles['Normal']))
    elements.append(Spacer(1, 0.25 * inch))

    # Invoice Title
    elements.append(Paragraph(f"TAX INVOICE", styles['Heading1']))
    elements.append(Spacer(1, 0.1 * inch))
    # Change this:
    # [f"Booking ID: #{invoice.booking.id}", f"Payment Status: {invoice.payment_status.upper()}"]

    # To this (More Reliable):
    status = "PAID" if invoice.booking.booking_status == 'confirmed' else invoice.payment_status.upper()

    invoice_data = [
        [f"Invoice No: {invoice.invoice_number}", f"Date: {invoice.generated_date.strftime('%d-%b-%Y')}"],
        [f"Booking ID: #{invoice.booking.id}", f"Payment Status: {status}"]
    ]
    invoice_table = Table(invoice_data, colWidths=[3 * inch, 3 * inch])
    invoice_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(invoice_table)
    elements.append(Spacer(1, 0.2 * inch))

    # Customer Details
    elements.append(Paragraph("BILL TO:", styles['Heading4']))
    customer_data = [
        [f"Name: {invoice.booking.user.name}", f"Email: {invoice.booking.user.email}"],
        [f"Phone: {invoice.booking.user.phone}",
         f"Location: {invoice.booking.user.city}, {invoice.booking.user.state}"],
    ]
    customer_table = Table(customer_data, colWidths=[3 * inch, 3 * inch])
    customer_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(customer_table)
    elements.append(Spacer(1, 0.2 * inch))

    # Package Details
    elements.append(Paragraph("PACKAGE DETAILS:", styles['Heading4']))
    package_data = [
        ["Package:", invoice.booking.package.title],
        ["Location:", invoice.booking.package.location],
        ["Duration:", invoice.booking.package.duration],
        ["Travel Date:", invoice.booking.travel_date],
        ["Travel Mode:", invoice.booking.get_travel_mode_display()],
        ["Package Type:", invoice.booking.get_package_type_display()],
        ["Number of Persons:", str(invoice.booking.persons)],
        ["Booking Status:", invoice.booking.booking_status.upper()],
    ]
    package_table = Table(package_data, colWidths=[1.5 * inch, 4.5 * inch])
    package_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(package_table)
    elements.append(Spacer(1, 0.2 * inch))

    # Passenger Details Section
    passengers = invoice.invoice_items.get('passenger_details', [])
    if passengers:
        elements.append(Paragraph("PASSENGER DETAILS:", styles['Heading4']))

        # Create passenger table
        passenger_data = [['S.No.', 'Name', 'Age', 'Gender', 'Aadhar Number']]
        for idx, passenger in enumerate(passengers, 1):
            passenger_data.append([
                str(idx),
                passenger['name'],
                str(passenger['age']),
                passenger['gender'],
                passenger['aadhar']
            ])

        passenger_table = Table(passenger_data, colWidths=[0.5 * inch, 2 * inch, 0.5 * inch, 1 * inch, 2 * inch])
        passenger_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (2, 0), (2, -1), 'CENTER'),
            ('ALIGN', (3, 0), (3, -1), 'CENTER'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(passenger_table)
        elements.append(Spacer(1, 0.2 * inch))
    else:
        elements.append(Paragraph("PASSENGER DETAILS: Not provided", styles['Heading4']))
        elements.append(Spacer(1, 0.1 * inch))

    # Cost Breakdown
    elements.append(Paragraph("COST BREAKDOWN:", styles['Heading4']))

    # Calculate values for display
    base_per_person = invoice.base_amount / invoice.booking.persons
    after_discount = float(invoice.package_type_amount) - float(invoice.discount_amount)

    cost_data = [
        ['Description', 'Amount (₹)'],
        [f'Base Price (₹{base_per_person:.0f} x {invoice.booking.persons} persons)',
         f'{invoice.base_amount:,.2f}'],
        [f'Package Type Multiplier ({invoice.package_type_multiplier}x)',
         f'{invoice.package_type_amount:,.2f}'],
        [f'Discount ({invoice.discount_percentage}%)',
         f'-{invoice.discount_amount:,.2f}'],
        ['Subtotal after discount', f'{after_discount:,.2f}'],
        [f'GST ({invoice.tax_percentage}%)', f'{invoice.tax_amount:,.2f}'],
        ['TOTAL AMOUNT', f'{invoice.total_amount:,.2f}']
    ]

    cost_table = Table(cost_data, colWidths=[4 * inch, 2 * inch])
    cost_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('LINEBELOW', (0, 0), (-1, -2), 1, colors.black),
        ('LINEABOVE', (0, -2), (-1, -2), 1, colors.black),
    ]))
    elements.append(cost_table)
    elements.append(Spacer(1, 0.2 * inch))

    # Inclusions and Exclusions
    elements.append(Paragraph("INCLUSIONS:", styles['Heading4']))
    for item in invoice.invoice_items.get('inclusions', []):
        elements.append(Paragraph(f"• {item}", styles['Normal']))

    elements.append(Spacer(1, 0.1 * inch))
    elements.append(Paragraph("EXCLUSIONS:", styles['Heading4']))
    for item in invoice.invoice_items.get('exclusions', []):
        elements.append(Paragraph(f"• {item}", styles['Normal']))

    elements.append(Spacer(1, 0.2 * inch))

    # Terms and Conditions
    elements.append(Paragraph("TERMS & CONDITIONS:", styles['Heading4']))
    terms = [
        "• This is a computer generated invoice - valid without signature",
        "• Payment must be made before the travel date",
        "• Cancellation charges apply as per company policy",
        "• Please carry a printout of this invoice during travel",
        "• For any queries, contact our customer support"
    ]
    if invoice.payment_status == 'refunded':
        terms.insert(1, "• This booking has been CANCELLED and amount refunded")

    for term in terms:
        elements.append(Paragraph(term, styles['Normal']))

    elements.append(Spacer(1, 0.3 * inch))

    # Footer
    elements.append(Paragraph("Thank you for choosing Travela!", styles['Center']))
    elements.append(Paragraph("We wish you a pleasant journey!", styles['Center']))

    # Build PDF
    doc.build(elements)

    buffer.seek(0)
    return buffer

def download_invoice(request, booking_id):
    """View to download invoice PDF"""
    user_id = request.session.get('user_id')
    if not user_id:
        messages.warning(request, "Please login to download invoice.")
        return redirect('login')

    try:
        booking = Booking.objects.get(id=booking_id, user_id=user_id)

        # Check if invoice exists
        try:
            invoice = Invoice.objects.get(booking=booking)
        except Invoice.DoesNotExist:
            # Generate new invoice only if booking is not cancelled or we want invoice for cancelled booking
            invoice = generate_invoice(booking)

        # Generate PDF
        pdf_buffer = create_invoice_pdf(invoice)

        # Return PDF as response
        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=f"Invoice_{invoice.invoice_number}.pdf",
            content_type='application/pdf'
        )

    except Booking.DoesNotExist:
        messages.error(request, "Booking not found.")
        return redirect('profile')


def view_invoice(request, booking_id):
    """View to display invoice in browser"""
    user_id = request.session.get('user_id')
    if not user_id:
        messages.warning(request, "Please login to view invoice.")
        return redirect('login')

    try:
        booking = Booking.objects.get(id=booking_id, user_id=user_id)

        # Check if invoice exists
        try:
            invoice = Invoice.objects.get(booking=booking)
        except Invoice.DoesNotExist:
            # Generate new invoice
            invoice = generate_invoice(booking)

        return render(request, 'invoice_view.html', {
            'invoice': invoice,
            'booking': booking
        })

    except Booking.DoesNotExist:
        messages.error(request, "Booking not found.")
        return redirect('profile')

# def register(request):
#     if request.method == "POST":
#         name = request.POST.get('name')
#         email = request.POST.get('email')
#         phone = request.POST.get('phone')
#         city = request.POST.get('city')
#         state = request.POST.get('state')
#         password = request.POST.get('password')
#
#         if UserRegistration.objects.filter(email=email).exists():
#             messages.error(request, "Email already registered!")
#             return redirect('register')
#
#         UserRegistration.objects.create(
#             name=name,
#             email=email,
#             phone=phone,
#             city=city,
#             state=state,
#             password=password
#         )
#
#         messages.success(request, "Registration Successful! Please Login.")
#         return redirect('login')
#
#     return render(request, "register.html")


def register(request):
    if request.method == "POST":
        # Get data from form
        user_data = {
            'name': request.POST.get('name'),
            'email': request.POST.get('email'),
            'phone': request.POST.get('phone'),
            'city': request.POST.get('city'),
            'state': request.POST.get('state'),
            'password': request.POST.get('password'),
        }

        if UserRegistration.objects.filter(email=user_data['email']).exists():
            messages.error(request, "Email already registered!")
            return redirect('register')

        # Generate 6-digit OTP
        otp = str(random.randint(100000, 999999))

        # Save OTP and User Data in Session
        request.session['temp_user_data'] = user_data
        request.session['registration_otp'] = otp

        # Send Email
        subject = "Verify your Travela Account"
        message = f"Hi {user_data['name']},\n\nYour OTP for registration is: {otp}\n\nHappy Traveling!"
        send_mail(subject, message, settings.EMAIL_HOST_USER, [user_data['email']])

        messages.info(request, "OTP sent to your email.")
        return redirect('verify_otp')

    return render(request, "register.html")


def verify_otp(request):
    if request.method == "POST":
        user_otp = request.POST.get('otp')
        saved_otp = request.session.get('registration_otp')
        user_data = request.session.get('temp_user_data')

        if user_otp == saved_otp:
            # Create the actual user
            UserRegistration.objects.create(
                name=user_data['name'],
                email=user_data['email'],
                phone=user_data['phone'],
                city=user_data['city'],
                state=user_data['state'],
                password=user_data['password']
            )
            # Clear session
            del request.session['registration_otp']
            del request.session['temp_user_data']

            messages.success(request, "Email Verified! Registration Successful.")
            return redirect('login')
        else:
            messages.error(request, "Invalid OTP. Please try again.")

    return render(request, "verify_otp.html")

def user_logout(request):
    logout(request)
    return redirect('home')


def home(request):
    # 1. Get all unique source cities for the dropdown filter
    source_cities = Package.objects.values_list('source_city', flat=True).distinct()

    # 2. Check for manual city filter from the dropdown
    selected_city = request.GET.get('city_filter')

    # 3. Get User Information
    user_id = request.session.get('user_id')
    user_city = None
    active_booking_package_ids = []
    cancelled_booking_package_ids = []

    if user_id:
        user = UserRegistration.objects.filter(id=user_id).first()
        if user:
            user_city = user.city

        # Get IDs for status badges
        active_booking_package_ids = list(
            Booking.objects.filter(
                user_id=user_id,
                booking_status__in=['confirmed', 'pending']
            ).values_list('package_id', flat=True)
        )

        cancelled_booking_package_ids = list(
            Booking.objects.filter(
                user_id=user_id,
                booking_status='cancelled'
            ).values_list('package_id', flat=True)
        )

    # 4. Filter Logic: Priority is Manual Dropdown > User's Profile City
    all_packages = Package.objects.all().order_by('-id')
    active_filter = selected_city or user_city

    if active_filter:
        city_filtered = all_packages.filter(source_city__iexact=active_filter)
        if city_filtered.exists():
            # If matches found for the city, show those (limit to 6 for home)
            packages = city_filtered[:6]
        else:
            # Fallback to all packages if no city match found
            packages = all_packages[:6]
    else:
        packages = all_packages[:6]


    feedbacks = Feedback.objects.filter(is_approved=True).order_by('-created_at')

    # Add the Verified Traveler badge logic
    for feedback in feedbacks:
        feedback.is_verified = Booking.objects.filter(
            user=feedback.user,
            booking_status='confirmed'
        ).exists()
    # --- NEW: TESTIMONIAL LOGIC END ---
    return render(request, 'index.html', {
        'packages': packages,
        'source_cities': source_cities,
        'active_filter': active_filter,
        'active_booking_package_ids': active_booking_package_ids,
        'cancelled_booking_package_ids': cancelled_booking_package_ids,
        'feedbacks': feedbacks,
    })

def about(request):
    return render(request, 'about.html')


def services(request):
    return render(request, 'services.html')


def packages(request):
    categories = Package.objects.values_list('category', flat=True).distinct()
    selected_category = request.GET.get('category')

    user_id = request.session.get('user_id')
    user_city = None

    if user_id:
        user = UserRegistration.objects.filter(id=user_id).first()
        if user:
            user_city = user.city

    # Start with all packages
    all_packages = Package.objects.all().order_by('-id')

    # Apply User City Filter
    # We use __iexact to ensure "vadodara" matches "Vadodara"
    if user_city:
        city_filtered = all_packages.filter(source_city__iexact=user_city)

        # Logic: If packages exist for the user's city, show them.
        # Otherwise, show all packages so the page isn't empty.
        if city_filtered.exists():
            all_packages = city_filtered
        else:
            # Optional: Set user_city to None if no matches found
            # so the "Showing packages from..." alert doesn't show misleading info
            user_city = f"{user_city} (No direct matches, showing all)"

    # Apply Category Filter
    if selected_category:
        all_packages = all_packages.filter(category=selected_category)

    # ... keep your existing pagination and booking logic ...
    paginator = Paginator(all_packages, 6)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'packages.html', {
        'packages': page_obj,
        'categories': categories,
        'selected_category': selected_category,
        'user_city': user_city,
    })
def login_view(request):
    if request.method == "POST":
        email = request.POST.get('email')
        password = request.POST.get('password')
        try:
            user = UserRegistration.objects.get(email=email, password=password)
            request.session['user_id'] = user.id
            request.session['user_name'] = user.name
            request.session['user_email'] = user.email
            messages.success(request, f"Welcome back, {user.name}!")
            return redirect('home')
        except UserRegistration.DoesNotExist:
            messages.error(request, "Invalid Email or Password")
    return render(request, 'login.html')


def calculate_price_with_package_type(package, persons, package_type):
    """Calculate price based on package type"""
    base_total = package.get_price_multiplier(persons)

    # Package type multipliers
    package_multipliers = {
        'standard': 1.0,  # No extra charge
        'premium': 1.2,  # 20% extra
        'vip': 1.5,  # 50% extra
    }

    multiplier = package_multipliers.get(package_type, 1.0)
    return int(base_total * multiplier)


def check_availability(request):
    """AJAX endpoint to check seat availability and calculate price"""
    if request.method == "POST":
        data = json.loads(request.body)
        package_id = data.get('package_id')
        persons = int(data.get('persons', 1))
        travel_mode = data.get('travel_mode', 'bus')
        package_type = data.get('package_type', 'standard')

        try:
            package = Package.objects.get(id=package_id)
            max_for_mode = package.get_max_persons_for_travel_mode(travel_mode)
            available, message = package.check_seat_availability(persons, travel_mode)

            # Calculate price with package type
            base_total = package.get_price_multiplier(persons) if available else 0
            total_price = calculate_price_with_package_type(package, persons, package_type) if available else 0

            return JsonResponse({
                'available': available,
                'message': message,
                'total_price': total_price,
                'base_total': base_total,
                'price_per_person': package.base_price,
                'available_seats': package.available_seats,
                'max_for_mode': max_for_mode,
                'vehicle_limit_message': f"Maximum {max_for_mode} persons for {travel_mode}"
            })
        except Package.DoesNotExist:
            return JsonResponse({'error': 'Package not found'}, status=404)

    return JsonResponse({'error': 'Invalid request'}, status=400)




# Add this helper function to calculate refund amount
def calculate_refund_amount(booking):
    """Calculate refund amount based on cancellation timing"""
    try:
        # Parse travel date (assuming format like "2024-03-26 10:30:00")
        travel_datetime = datetime.strptime(booking.travel_date, "%Y-%m-%d %H:%M:%S")
        # Make it timezone aware
        if timezone.is_naive(travel_datetime):
            travel_datetime = timezone.make_aware(travel_datetime)

        current_datetime = timezone.now()
        time_difference = travel_datetime - current_datetime
        hours_until_travel = time_difference.total_seconds() / 3600
        days_until_travel = hours_until_travel / 24

        refund_percentage = 0
        refund_message = ""

        if hours_until_travel < 24:
            refund_percentage = 0
            refund_message = "No refund (cancellation within 24 hours of travel)"
        elif days_until_travel < 4:  # Less than 4 days but more than 24 hours
            refund_percentage = 50
            refund_message = f"50% refund (cancellation {int(days_until_travel)} days before travel)"
        elif days_until_travel >= 7:
            refund_percentage = 70
            refund_message = f"70% refund (cancellation {int(days_until_travel)} days before travel)"
        elif days_until_travel >= 4:
            refund_percentage = 50
            refund_message = f"50% refund (cancellation {int(days_until_travel)} days before travel)"

        refund_amount = (booking.total_price * refund_percentage) / 100

        return {
            'refund_percentage': refund_percentage,
            'refund_amount': refund_amount,
            'refund_message': refund_message,
            'hours_until_travel': int(hours_until_travel),
            'days_until_travel': round(days_until_travel, 1)
        }
    except Exception as e:
        # If date parsing fails, return default values
        return {
            'refund_percentage': 0,
            'refund_amount': 0,
            'refund_message': "Unable to calculate refund - please contact support",
            'hours_until_travel': 0,
            'days_until_travel': 0
        }


# Update your cancel_booking function
# @require_POST
# def cancel_booking(request, booking_id):
#     """User cancels their own booking"""
#     user_id = request.session.get('user_id')
#     if not user_id:
#         return JsonResponse({'error': 'Please login'}, status=401)
#
#     try:
#         booking = Booking.objects.get(id=booking_id, user_id=user_id)
#
#         if booking.booking_status == 'cancelled':
#             return JsonResponse({'error': 'Booking already cancelled'}, status=400)
#
#         # Calculate refund amount
#         refund_info = calculate_refund_amount(booking)
#
#         # Update package available seats
#         package = booking.package
#         package.available_seats += booking.persons
#         package.save()
#
#         # Update booking status
#         booking.booking_status = 'cancelled'
#         booking.save()
#
#         # Update or create invoice with refund status
#         try:
#             invoice = Invoice.objects.get(booking=booking)
#             invoice.payment_status = 'refunded'
#             invoice.save()
#         except Invoice.DoesNotExist:
#             # Generate invoice with refund status
#             invoice = generate_invoice(booking)
#
#         # Prepare success message with refund info
#         if refund_info['refund_amount'] > 0:
#             success_message = f"Booking cancelled. Refund of ₹{refund_info['refund_amount']:,.2f} ({refund_info['refund_percentage']}%) will be processed within 7-10 business days."
#         else:
#             success_message = f"Booking cancelled. No refund applicable as per cancellation policy."
#
#         messages.success(request, success_message)
#
#         return JsonResponse({
#             'success': True,
#             'message': success_message,
#             'refund_info': refund_info,
#             'available_seats': package.available_seats
#         })
#
#     except Booking.DoesNotExist:
#         return JsonResponse({'error': 'Booking not found'}, status=404)

# Add new view for cancellation policy page
def cancellation_policy(request, booking_id):
    """Display cancellation policy and refund calculation for a specific booking"""
    user_id = request.session.get('user_id')
    if not user_id:
        messages.warning(request, "Please login to view cancellation policy.")
        return redirect('login')

    try:
        booking = Booking.objects.get(id=booking_id, user_id=user_id)

        if booking.booking_status == 'cancelled':
            messages.info(request, "This booking has already been cancelled.")
            return redirect('my_bookings')

        # Calculate refund based on current time
        refund_info = calculate_refund_amount(booking)

        # Calculate refund for different scenarios (for display purposes)
        scenarios = {
            'within_24h': {
                'hours': 23,
                'refund_percentage': 0,
                'refund_amount': 0,
                'description': 'Less than 24 hours before travel'
            },
            'less_than_4days': {
                'hours': 72,  # 3 days
                'refund_percentage': 50,
                'refund_amount': (booking.total_price * 50) / 100,
                'description': 'Between 24 hours and 4 days before travel'
            },
            'more_than_7days': {
                'hours': 192,  # 8 days
                'refund_percentage': 70,
                'refund_amount': (booking.total_price * 70) / 100,
                'description': '7 or more days before travel'
            }
        }

        return render(request, 'cancellation_policy.html', {
            'booking': booking,
            'refund_info': refund_info,
            'scenarios': scenarios,
            'total_paid': booking.total_price
        })

    except Booking.DoesNotExist:
        messages.error(request, "Booking not found.")
        return redirect('my_bookings')

def my_bookings(request):
    """View for users to see their bookings"""
    user_id = request.session.get('user_id')
    if not user_id:
        messages.warning(request, "Please login to view your bookings.")
        return redirect('login')

    bookings = Booking.objects.filter(user_id=user_id).select_related('package').order_by('-booking_date')

    return render(request, 'my_bookings.html', {
        'bookings': bookings
    })


# Admin cancellation endpoints
@staff_member_required
def admin_cancel_booking(request, booking_id):
    """Admin endpoint to cancel booking"""
    if request.method == 'GET':
        try:
            booking = Booking.objects.get(id=booking_id)

            if booking.booking_status != 'cancelled':
                # Increase available seats
                booking.package.available_seats += booking.persons
                booking.package.save()

                # Update booking status
                booking.booking_status = 'cancelled'
                booking.save()

                messages.success(request,
                                 f"Booking #{booking_id} cancelled successfully. {booking.persons} seats released.")
            else:
                messages.warning(request, "Booking already cancelled.")

        except Booking.DoesNotExist:
            messages.error(request, "Booking not found.")

    return redirect('admin:app_booking_changelist')


@staff_member_required
def admin_confirm_booking(request, booking_id):
    """Admin endpoint to confirm booking"""
    if request.method == 'GET':
        try:
            booking = Booking.objects.get(id=booking_id)
            booking.booking_status = 'confirmed'
            booking.save()
            messages.success(request, f"Booking #{booking_id} confirmed successfully.")
        except Booking.DoesNotExist:
            messages.error(request, "Booking not found.")

    return redirect('admin:app_booking_changelist')

def blog(request):
    return render(request, 'blog.html')


def destination(request):
    return render(request, 'destination.html')


def tour(request):
    return render(request, 'tour.html')


def booking(request):
    return render(request, 'booking.html')


def gallery(request):
    return render(request, 'gallery.html')


def guides(request):
    return render(request, 'guides.html')


def testimonial(request):
    return render(request, 'testimonial.html')


# def contact(request):
#     return render(request, 'contact.html')




def contact(request):
    if request.method == 'POST':
        # Create a new ContactMessage instance
        contact_message = ContactMessage(
            name=request.POST.get('name'),
            email=request.POST.get('email'),
            subject=request.POST.get('subject'),
            message=request.POST.get('message')
        )
        contact_message.save()

        # Add a success message
        messages.success(request, 'Your message has been sent successfully!')
        return redirect('contact')  # or wherever you want to redirect

    return render(request, 'contact.html')
def subscribe(request):
    if request.method == "POST":
        email = request.POST.get('email')
        messages.success(request, f"Thank you for subscribing with {email}!")
    return HttpResponseRedirect(reverse('home'))


def error404(request):
    return render(request, '404.html')


def package_list(request):
    # This can now simply redirect to home or use the same logic without the [:6] slice
    return home(request)


def profile_view(request):
    user_id = request.session.get('user_id')
    if not user_id:
        messages.warning(request, "Please login to view your profile.")
        return redirect('login')

    user = get_object_or_404(UserRegistration, id=user_id)
    bookings = Booking.objects.filter(user=user).order_by('-booking_date')

    # Calculate booking statistics properly
    total_bookings = bookings.count()

    # Count by status
    confirmed_count = bookings.filter(booking_status='confirmed').count()
    pending_count = bookings.filter(booking_status='pending').count()
    cancelled_count = bookings.filter(booking_status='cancelled').count()

    # ACTIVE = Confirmed + Pending (NOT cancelled)
    active_count = confirmed_count + pending_count

    return render(request, 'profile.html', {
        'user': user,
        'bookings': bookings,
        'total_bookings': total_bookings,
        'confirmed_count': confirmed_count,
        'pending_count': pending_count,
        'cancelled_count': cancelled_count,
        'active_count': active_count,  # This will correctly show 2 (not 5)
    })



def package_detail(request, pk):
    package = get_object_or_404(Package, pk=pk)
    day_plans = package.day_plans.all()

    # Calculate time remaining
    now = timezone.now()
    if package.triptime and package.triptime > now:
        time_diff = package.triptime - now
        days_remaining = time_diff.days
        # Calculate remaining hours after subtracting full days
        hours_remaining = time_diff.seconds // 3600
    else:
        days_remaining = 0
        hours_remaining = 0

    return render(request, 'package_detail.html', {
        'package': package,
        'day_plans': day_plans,
        'days_remaining': days_remaining,
        'hours_remaining': hours_remaining,
    })

# def package_detail(request, pk):
#     package = get_object_or_404(Package, pk=pk)
#     # Prefetching day plans to display on the detail page
#     day_plans = package.day_plans.all()
#
#     return render(request, 'package_detail.html', {
#         'package': package,
#         'day_plans': day_plans,
#     })
#

def create_cancellation_invoice_pdf(invoice):
    """Generate PDF for cancellation invoice"""
    buffer = io.BytesIO()

    # Create the PDF object
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=72, leftMargin=72,
                            topMargin=72, bottomMargin=18)

    # Container for the 'Flowable' objects
    elements = []

    # Styles
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Center', alignment=TA_CENTER))
    styles.add(ParagraphStyle(name='Right', alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='Refund', textColor=colors.green, fontSize=20, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name='Charges', textColor=colors.red, fontSize=16))
    styles.add(ParagraphStyle(name='RefundText', textColor=colors.green, fontSize=14, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name='ChargesText', textColor=colors.red, fontSize=14, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name='SectionHeader', fontSize=14, textColor=colors.blue, spaceAfter=12, spaceBefore=12))
    styles.add(ParagraphStyle(name='Info', fontSize=10, textColor=colors.gray))

    # Company Header
    elements.append(Paragraph("TRAVELA TOURS & TRAVELS", styles['Title']))
    elements.append(Paragraph("123 Travel Street, Tourism Hub, Mumbai - 400001", styles['Normal']))
    elements.append(Paragraph("Phone: +91 98765 43210 | Email: info@travela.com", styles['Normal']))
    elements.append(Paragraph("GSTIN: 27ABCDE1234F1Z5", styles['Normal']))
    elements.append(Spacer(1, 0.25 * inch))

    # Invoice Title
    elements.append(Paragraph("CANCELLATION INVOICE", styles['Heading1']))
    elements.append(Paragraph("BOOKING CANCELLED", styles['Heading2']))
    elements.append(Spacer(1, 0.1 * inch))

    # Invoice Number and Date
    invoice_data = [
        [f"Invoice No: {invoice.invoice_number}", f"Cancellation Date: {invoice.generated_date.strftime('%d-%b-%Y')}"],
        [f"Booking ID: #{invoice.booking.id}",
         f"Original Booking Date: {invoice.booking.booking_date.strftime('%d-%b-%Y')}"]
    ]
    invoice_table = Table(invoice_data, colWidths=[3 * inch, 3 * inch])
    invoice_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, -1), colors.lightgrey),
    ]))
    elements.append(invoice_table)
    elements.append(Spacer(1, 0.2 * inch))

    # Customer Details
    elements.append(Paragraph("BILL TO:", styles['Heading4']))
    customer_data = [
        [f"Name: {invoice.booking.user.name}", f"Email: {invoice.booking.user.email}"],
        [f"Phone: {invoice.booking.user.phone}",
         f"Location: {invoice.booking.user.city}, {invoice.booking.user.state}"],
    ]
    customer_table = Table(customer_data, colWidths=[3 * inch, 3 * inch])
    customer_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, -1), colors.lightblue),
    ]))
    elements.append(customer_table)
    elements.append(Spacer(1, 0.2 * inch))

    # Cancellation Details
    cancellation = invoice.invoice_items.get('cancellation_details', {})
    if cancellation:
        # Refund Summary Box
        refund_summary_data = [
            ["REFUND SUMMARY", "", ""],
            ["Refund Percentage", f"{cancellation.get('refund_percentage', 0)}%",
             f"Refund Amount: ₹{cancellation.get('refund_amount', 0):,.2f}"],
            ["Cancellation Timing", f"{cancellation.get('days_until_travel', 0)} days before travel",
             f"({cancellation.get('hours_until_travel', 0)} hours)"],
            ["Cancellation Charges", f"₹{cancellation.get('cancellation_charges', 0):,.2f}",
             f"Original Amount: ₹{invoice.total_amount:,.2f}"],
        ]
        refund_table = Table(refund_summary_data, colWidths=[2 * inch, 2 * inch, 2 * inch])
        refund_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.blue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 14),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('BACKGROUND', (0, 1), (-1, -1), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('TEXTCOLOR', (2, 1), (2, 1), colors.green),
            ('FONTNAME', (2, 1), (2, 1), 'Helvetica-Bold'),
            ('TEXTCOLOR', (1, 3), (1, 3), colors.red),
        ]))
        elements.append(refund_table)
        elements.append(Spacer(1, 0.2 * inch))

        # Refund Message
        elements.append(Paragraph(f"<i>{cancellation.get('refund_message', '')}</i>", styles['Italic']))
        elements.append(Spacer(1, 0.2 * inch))

    # Package Details
    elements.append(Paragraph("PACKAGE DETAILS:", styles['SectionHeader']))
    package_data = [
        ["Package:", invoice.booking.package.title],
        ["Location:", invoice.booking.package.location],
        ["Duration:", invoice.booking.package.duration],
        ["Original Travel Date:", invoice.booking.travel_date],
        ["Travel Mode:", invoice.booking.get_travel_mode_display()],
        ["Package Type:", invoice.booking.get_package_type_display()],
        ["Number of Persons:", str(invoice.booking.persons)],
        ["Booking Status:", "CANCELLED"],
    ]
    package_table = Table(package_data, colWidths=[1.5 * inch, 4.5 * inch])
    package_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW', (0, 0), (-1, -2), 1, colors.lightgrey),
    ]))
    elements.append(package_table)
    elements.append(Spacer(1, 0.2 * inch))

    # Passenger Details Section (for cancelled booking)
    passengers = invoice.invoice_items.get('passenger_details', [])
    if passengers:
        elements.append(Paragraph("PASSENGER DETAILS (at time of booking):", styles['SectionHeader']))

        # Create passenger table
        passenger_data = [['S.No.', 'Name', 'Age', 'Gender', 'Aadhar Number']]
        for idx, passenger in enumerate(passengers, 1):
            passenger_data.append([
                str(idx),
                passenger['name'],
                str(passenger['age']),
                passenger['gender'],
                passenger['aadhar']
            ])

        passenger_table = Table(passenger_data, colWidths=[0.5 * inch, 2 * inch, 0.5 * inch, 1 * inch, 2 * inch])
        passenger_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (2, 0), (2, -1), 'CENTER'),
            ('ALIGN', (3, 0), (3, -1), 'CENTER'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(passenger_table)
        elements.append(Spacer(1, 0.2 * inch))

    # Original Cost Breakdown
    elements.append(Paragraph("ORIGINAL COST BREAKDOWN:", styles['SectionHeader']))

    # Calculate values for display
    base_per_person = invoice.base_amount / invoice.booking.persons
    after_discount = float(invoice.package_type_amount) - float(invoice.discount_amount)

    cost_data = [
        ['Description', 'Amount (₹)'],
        [f'Base Price (₹{base_per_person:.0f} x {invoice.booking.persons} persons)',
         f'{invoice.base_amount:,.2f}'],
        [f'Package Type Multiplier ({invoice.package_type_multiplier}x)',
         f'{invoice.package_type_amount:,.2f}'],
        [f'Discount ({invoice.discount_percentage}%)',
         f'-{invoice.discount_amount:,.2f}'],
        ['Subtotal after discount', f'{after_discount:,.2f}'],
        [f'GST ({invoice.tax_percentage}%)', f'{invoice.tax_amount:,.2f}'],
        ['ORIGINAL TOTAL AMOUNT', f'{invoice.total_amount:,.2f}']
    ]

    cost_table = Table(cost_data, colWidths=[4 * inch, 2 * inch])
    cost_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('LINEBELOW', (0, 0), (-1, -2), 1, colors.black),
        ('LINEABOVE', (0, -2), (-1, -2), 1, colors.black),
    ]))
    elements.append(cost_table)
    elements.append(Spacer(1, 0.2 * inch))

    # Cancellation Summary
    if cancellation:
        elements.append(Paragraph("CANCELLATION SUMMARY:", styles['SectionHeader']))

        cancellation_data = [
            ['Description', 'Amount (₹)'],
            ['Original Booking Amount', f'{invoice.total_amount:,.2f}'],
            [f'Cancellation Charges ({100 - cancellation.get("refund_percentage", 0)}%)',
             f'-{cancellation.get("cancellation_charges", 0):,.2f}'],
            ['REFUND AMOUNT', f'{cancellation.get("refund_amount", 0):,.2f}']
        ]

        cancellation_table = Table(cancellation_data, colWidths=[4 * inch, 2 * inch])
        cancellation_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('BACKGROUND', (0, -1), (-1, -1), colors.green),
            ('TEXTCOLOR', (1, -1), (1, -1), colors.white),
            ('LINEBELOW', (0, 1), (-1, -2), 1, colors.black),
            ('TEXTCOLOR', (1, 2), (1, 2), colors.red),
        ]))
        elements.append(cancellation_table)
        elements.append(Spacer(1, 0.2 * inch))

    # Cancellation Policy Applied
    elements.append(Paragraph("CANCELLATION POLICY APPLIED:", styles['SectionHeader']))

    policy_data = [
        ['Cancellation Period', 'Refund %', 'Status'],
        ['7 or more days before travel', '70%',
         '✓ Applied' if cancellation.get('days_until_travel', 0) >= 7 else 'Not Applied'],
        ['4-7 days before travel', '50%',
         '✓ Applied' if 4 <= cancellation.get('days_until_travel', 0) < 7 else 'Not Applied'],
        ['24 hours to 4 days before travel', '50%',
         '✓ Applied' if cancellation.get('hours_until_travel', 0) >= 24 and cancellation.get('days_until_travel',
                                                                                             0) < 4 else 'Not Applied'],
        ['Less than 24 hours before travel', '0%',
         '✓ Applied' if cancellation.get('hours_until_travel', 0) < 24 else 'Not Applied'],
    ]

    policy_table = Table(policy_data, colWidths=[2.5 * inch, 1.5 * inch, 2 * inch])
    policy_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.blue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('BACKGROUND', (0, 1), (-1, 1), colors.lightgrey if policy_data[1][2] == 'Not Applied' else colors.lightgreen),
        ('BACKGROUND', (0, 2), (-1, 2), colors.lightgrey if policy_data[2][2] == 'Not Applied' else colors.lightgreen),
        ('BACKGROUND', (0, 3), (-1, 3), colors.lightgrey if policy_data[3][2] == 'Not Applied' else colors.lightgreen),
        ('BACKGROUND', (0, 4), (-1, 4), colors.lightgrey if policy_data[4][2] == 'Not Applied' else colors.lightgreen),
    ]))
    elements.append(policy_table)
    elements.append(Spacer(1, 0.2 * inch))

    # Refund Processing Information
    elements.append(Paragraph("REFUND PROCESSING:", styles['SectionHeader']))
    refund_processing = [
        [
            f"• Refund amount of ₹{cancellation.get('refund_amount', 0):,.2f} will be processed within 7-10 business days"],
        ["• Refund will be credited to the original payment method used for booking"],
        ["• Once processed, the refund status will be updated in your account"],
        ["• For any queries regarding refund, please contact our customer support"]
    ]
    for item in refund_processing:
        elements.append(Paragraph(item[0], styles['Normal']))
    elements.append(Spacer(1, 0.2 * inch))

    # Terms and Conditions
    elements.append(Paragraph("TERMS & CONDITIONS:", styles['SectionHeader']))
    terms = [
        "• This is a computer generated cancellation invoice - valid without signature",
        "• The booking has been cancelled as per the cancellation policy",
        "• Refund will be processed as per the timeline mentioned above",
        "• Seats have been released and are available for other travelers",
        "• For any queries, contact our customer support"
    ]
    for term in terms:
        elements.append(Paragraph(term, styles['Normal']))

    elements.append(Spacer(1, 0.3 * inch))

    # Footer
    elements.append(Paragraph("We hope to serve you better next time!", styles['Center']))
    elements.append(Paragraph("Thank you for choosing Travela", styles['Center']))
    elements.append(Spacer(1, 0.1 * inch))
    elements.append(Paragraph("This is a system generated cancellation invoice", styles['Info']))

    # Build PDF
    doc.build(elements)

    buffer.seek(0)
    return buffer


def download_cancellation_invoice(request, booking_id):
    """View to download cancellation invoice PDF"""
    user_id = request.session.get('user_id')
    if not user_id:
        messages.warning(request, "Please login to download invoice.")
        return redirect('login')

    try:
        booking = Booking.objects.get(id=booking_id, user_id=user_id)

        if booking.booking_status != 'cancelled':
            messages.warning(request, "This booking is not cancelled.")
            return redirect('profile')

        # Check if invoice exists
        try:
            invoice = Invoice.objects.get(booking=booking)
        except Invoice.DoesNotExist:
            # Generate new invoice
            invoice = generate_invoice(booking)

        # Generate PDF for cancellation invoice
        pdf_buffer = create_cancellation_invoice_pdf(invoice)

        # Return PDF as response
        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=f"Cancellation_Invoice_{invoice.invoice_number}.pdf",
            content_type='application/pdf'
        )

    except Booking.DoesNotExist:
        messages.error(request, "Booking not found.")
        return redirect('profile')


def view_cancellation_invoice(request, booking_id):
    """View to display cancellation invoice in browser"""
    user_id = request.session.get('user_id')
    if not user_id:
        messages.warning(request, "Please login to view invoice.")
        return redirect('login')

    try:
        booking = Booking.objects.get(id=booking_id, user_id=user_id)

        if booking.booking_status != 'cancelled':
            messages.warning(request, "This booking is not cancelled.")
            return redirect('profile')

        # Check if invoice exists
        try:
            invoice = Invoice.objects.get(booking=booking)
        except Invoice.DoesNotExist:
            # Generate new invoice
            invoice = generate_invoice(booking)

        return render(request, 'cancellation_invoice.html', {
            'invoice': invoice,
            'booking': booking
        })

    except Booking.DoesNotExist:
        messages.error(request, "Booking not found.")
        return redirect('profile')





def check_package_availability(request, pk):
    """AJAX endpoint to check package availability"""
    try:
        package = Package.objects.get(pk=pk)
        return JsonResponse({
            'available_seats': package.available_seats,
            'is_expired': package.is_trip_expired(),
            'trip_status': package.get_trip_status()
        })
    except Package.DoesNotExist:
        return JsonResponse({'error': 'Package not found'}, status=404)




def generate_upi_qr(upi_id, name, amount, note):
    """Generates a base64 encoded QR code image for UPI payment"""
    upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}&tn={note}&cu=INR"
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(upi_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


# def booking_view(request, pk):
#     user_id = request.session.get('user_id')
#     if not user_id:
#         return redirect('login')
#
#     package = get_object_or_404(Package, pk=pk)
#     user = get_object_or_404(UserRegistration, id=user_id)
#
#     if request.method == "POST":
#         persons = int(request.POST.get('persons'))
#         travel_mode = request.POST.get('travel_mode')
#         package_type = request.POST.get('package_type')
#
#         # Calculate price (simplified for example)
#         multiplier = {'standard': 1.0, 'premium': 1.2, 'vip': 1.5}.get(package_type, 1.0)
#         total_price = int(package.base_price * persons * multiplier)
#
#         with transaction.atomic():
#             booking = Booking.objects.create(
#                 user=user,
#                 package=package,
#                 persons=persons,
#                 travel_mode=travel_mode,
#                 package_type=package_type,
#                 total_price=total_price,
#                 booking_status='pending',  # Wait for payment screenshot
#                 travel_date=package.triptime.strftime("%Y-%m-%d %H:%M:%S")
#             )
#
#             # Save Passenger Details
#             for i in range(persons):
#                 name = request.POST.get(f'passenger_name_{i}')
#                 age = request.POST.get(f'passenger_age_{i}')
#                 gender = request.POST.get(f'passenger_gender_{i}')
#                 if name:
#                     PassengerDetail.objects.create(
#                         booking=booking, name=name, age=age, gender=gender
#                     )
#
#             # Reduce seats
#             package.available_seats -= persons
#             package.save()
#
#         # Redirect to the QR Payment Page
#         return redirect('payment_page', booking_id=booking.id)
#
#     return render(request, 'booking.html', {
#         'package': package,
#         'travel_modes': package.get_available_travel_modes()
#     })


# In your payment_page function in views.py
def booking_view(request, pk):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    package = get_object_or_404(Package, pk=pk)
    user = get_object_or_404(UserRegistration, id=user_id)

    if request.method == "POST":
        persons = int(request.POST.get('persons'))
        travel_mode = request.POST.get('travel_mode')
        package_type = request.POST.get('package_type')

        multiplier = {'standard': 1.0, 'premium': 1.2, 'vip': 1.5}.get(package_type, 1.0)
        total_price = int(package.base_price * persons * multiplier)

        with transaction.atomic():
            # WAITING LIST LOGIC
            if package.available_seats <= 0:
                current_status = 'waiting'
                # Calculate WL Number based on existing waiting bookings
                waiting_count = Booking.objects.filter(
                    package=package,
                    booking_status='waiting'
                ).count()
                wl_number = waiting_count + 1
                wait_message = f"WL{wl_number}"
                waiting_list_position = wl_number
            else:
                current_status = 'pending'
                wait_message = ""
                waiting_list_position = None

            booking = Booking.objects.create(
                user=user,
                package=package,
                persons=persons,
                travel_mode=travel_mode,
                package_type=package_type,
                total_price=total_price,
                booking_status=current_status,
                travel_date=package.triptime.strftime("%Y-%m-%d %H:%M:%S"),
                message=wait_message,
                waiting_list_position=waiting_list_position
            )

            # Save passenger details
            for i in range(persons):
                name = request.POST.get(f'passenger_name_{i}')
                age = request.POST.get(f'passenger_age_{i}')
                gender = request.POST.get(f'passenger_gender_{i}')
                aadhar = request.POST.get(f'passenger_aadhar_{i}')

                if name and age and gender:
                    PassengerDetail.objects.create(
                        booking=booking,
                        name=name,
                        age=age,
                        gender=gender,
                        aadhar_number=aadhar if aadhar else None
                    )

            # Only reduce seats if NOT on waiting list
            if current_status != 'waiting':
                package.available_seats -= persons
                package.save()

        if current_status == 'waiting':
            messages.warning(
                request,
                f"Package is fully booked! You have been added to the Waiting List at position: {wait_message}"
            )
        else:
            messages.success(request, "Booking created successfully! Please complete payment.")

        return redirect('payment_page', booking_id=booking.id)

    # Calculate days and hours remaining for the template
    now = timezone.now()
    if package.triptime and package.triptime > now:
        time_diff = package.triptime - now
        days_remaining = time_diff.days
        hours_remaining = time_diff.seconds // 3600
    else:
        days_remaining = 0
        hours_remaining = 0

    return render(request, 'booking.html', {
        'package': package,
        'travel_modes': package.get_available_travel_modes(),
        'days_remaining': days_remaining,
        'hours_remaining': hours_remaining,
        'is_trip_expired': package.is_trip_expired(),
    })


@require_POST
def cancel_booking(request, booking_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'error': 'Please login'}, status=401)

    try:
        with transaction.atomic():
            booking = Booking.objects.get(id=booking_id, user_id=user_id)

            if booking.booking_status == 'cancelled':
                return JsonResponse({'error': 'Already cancelled'}, status=400)

            package = booking.package
            old_status = booking.booking_status

            # Mark as cancelled
            booking.booking_status = 'cancelled'
            booking.save()

            promotion_log = ""

            # If the cancelled booking was NOT a waiting booking, we have a free seat
            if old_status != 'waiting':
                # Check for the next person in Waiting List
                next_waiting = Booking.objects.filter(
                    package=package,
                    booking_status='waiting'
                ).order_by('booking_date').first()

                if next_waiting and next_waiting.persons <= package.available_seats + booking.persons:
                    # PROMOTE USER FROM WAITING LIST
                    next_waiting.booking_status = 'pending'
                    next_waiting.promoted_from_waiting = True
                    next_waiting.message = "Promoted from Waiting List"
                    next_waiting.save()

                    # Reduce available seats for the promoted booking
                    package.available_seats = package.available_seats - next_waiting.persons + booking.persons
                    package.save()

                    promotion_log = f" Seat assigned to {next_waiting.user.name} from waiting list."

                    # Update remaining waiting list positions
                    update_waiting_list_positions(package)
                else:
                    # No one waiting or not enough seats, return seat to pool
                    package.available_seats += booking.persons
                    package.save()
                    promotion_log = " Seat returned to availability."
            else:
                # Cancelled waiting list booking - just update positions
                promotion_log = " Waiting list position cleared."
                update_waiting_list_positions(package)

        messages.success(request, f"Booking cancelled successfully.{promotion_log}")
        return JsonResponse({'success': True, 'message': f"Booking cancelled.{promotion_log}"})

    except Booking.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)


def update_waiting_list_positions(package):
    """Helper function to update waiting list positions"""
    waiting_bookings = Booking.objects.filter(
        package=package,
        booking_status='waiting'
    ).order_by('booking_date')

    for idx, booking in enumerate(waiting_bookings, 1):
        booking.waiting_list_position = idx
        booking.message = f"WL{idx}"
        booking.save(update_fields=['waiting_list_position', 'message'])


def check_waiting_list_status(request, package_id):
    """AJAX endpoint to check waiting list status"""
    try:
        package = Package.objects.get(id=package_id)
        waiting_count = Booking.objects.filter(
            package=package,
            booking_status='waiting'
        ).count()

        return JsonResponse({
            'waiting_count': waiting_count,
            'available_seats': package.available_seats,
            'is_full': package.available_seats <= 0
        })
    except Package.DoesNotExist:
        return JsonResponse({'error': 'Package not found'}, status=404)
def payment_page(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)

    if request.method == "POST":
        screenshot = request.FILES.get('screenshot')
        if screenshot:
            booking.payment_screenshot = screenshot
            booking.save()
            return render(request, 'payment_success.html', {'booking': booking})

    # 1. Define the raw UPI URL (This is the "Magic Link" for autofill)
    upi_id = "arshadpirzada19@okhdfcbank"
    name = "Travela Tours"
    amount = booking.total_price
    note = f"Booking_{booking.id}"

    upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}&tn={note}&cu=INR"

    # 2. Generate the QR as usual for desktop users
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(upi_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    qr_code = base64.b64encode(buffer.getvalue()).decode()

    return render(request, 'payment.html', {
        'booking': booking,
        'qr_code': qr_code,
        'upi_url': upi_url  # Pass the raw link here
    })


def testimonial_view(request):
    if request.method == "POST":
        user_id = request.session.get('user_id')  #

        # 1. Check if user is logged in
        if not user_id:
            messages.error(request, "Please login to share your experience!")
            return redirect('login')  #

        message_text = request.POST.get('message', '').strip()
        rating_value = request.POST.get('rating')

        try:
            user = UserRegistration.objects.get(id=user_id)  #

            # 2. Professional Check: One review per user
            if Feedback.objects.filter(user=user).exists():
                messages.warning(request, "You have already shared your feedback! Thank you.")
                return redirect('home')

            # 3. Save if data is valid
            if message_text and rating_value:
                Feedback.objects.create(
                    user=user,
                    message=message_text,
                    rating=int(rating_value)
                )
                messages.success(request, "Thank you! Your feedback is sent for approval.")
            else:
                messages.error(request, "Please provide a message and a rating.")

        except UserRegistration.DoesNotExist:
            messages.error(request, "User account not found.")

        return redirect('home')

    return redirect('home')




def edit_testimonial(request, feedback_id):
    feedback = get_object_or_404(Feedback, id=feedback_id, user_id=request.session.get('user_id'))
    if request.method == "POST":
        feedback.message = request.POST.get('message')
        feedback.rating = request.POST.get('rating')
        feedback.is_approved = False  # Re-verify after edit
        feedback.save()
        messages.success(request, "Feedback updated and sent for re-approval!")
        return redirect('home')
    return redirect('home')

def delete_testimonial(request, feedback_id):
    feedback = get_object_or_404(Feedback, id=feedback_id, user_id=request.session.get('user_id'))
    feedback.delete()
    messages.success(request, "Feedback deleted successfully.")
    return redirect('home')





# IMPORT YOUR MODELS HERE

# Configure Gemini with your Free API Key
genai.configure(api_key="AIzaSyCxXeWlNtYuKaX79s7zleQ-QXcrgt6_MQE")


# @csrf_exempt
# def travel_chatbot(request):
#     if request.method == "POST":
#         try:
#             data = json.loads(request.body)
#             user_message = data.get('message', '').strip()
#             user_id = request.session.get('user_id')
#
#             # 1. ENHANCED KNOWLEDGE BASE
#             comparison_kb = """
#             You are the EasyGo AI Assistant.
#
#             HOW TO BOOK A TRIP:
#             1. Login: Click 'Login' in the top bar.
#             2. Choose: Go to 'Packages' and click 'View Details'.
#             3. Fill Form: Enter traveler names, ages, and choose Bus/Flight/Train.
#             4. Pay: Use the QR code to pay via UPI.
#             5. Confirm: Upload the payment screenshot to get your confirmed status.
#
#             WHY EASYGO? (Comparison Table Data):
#             - Group Discounts: 5% (6+), 10% (11+), 15% (21+).
#             - Reviews: 100% Verified travelers only.
#             - Waitlist: Auto-promotion system.
#             """
#
#             # 2. Database Context with Seat Counts
#             packages = Package.objects.all()
#             pkg_data = [f"{p.title} (₹{p.base_price}) - Available Seats: {p.available_seats}" for p in packages]
#
#             # Safe User Logic (Prevents the crash)
#             user_name = "Guest"
#             if user_id:
#                 user_obj = UserRegistration.objects.filter(id=user_id).first()
#                 if user_obj:
#                     user_name = user_obj.name or "Traveler"
#
#             # 3. Model Logic (Updated to gemini-2.5-flash)
#             # Use 'gemini-1.5-flash' ONLY if you are on an older SDK version
#             try:
#                 model = genai.GenerativeModel('gemini-2.5-flash')
#             except:
#                 model = genai.GenerativeModel('gemini-1.5-flash')
#
#             prompt = (
#                 f"Instructions: {comparison_kb}\n"
#                 f"Packages: {pkg_data}\n"
#                 f"User: {user_name}\n"
#                 f"Query: {user_message}\n"
#                 f"Rule: If comparing, use a Markdown Table. If listing trips, show Available Seats."
#             )
#
#             response = model.generate_content(prompt)
#             return JsonResponse({'reply': response.text})
#
#         except Exception as e:
#             # This logs the REAL error to your terminal so you can see it
#             import traceback
#             print(traceback.format_exc())
#             return JsonResponse({'reply': "I'm ready to help! Ask me about our 'Best Packages' or how to book a trip."})
#
#     return JsonResponse({'error': 'Invalid request'}, status=400)

@csrf_exempt
def travel_chatbot(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            user_message = data.get('message', '').strip().lower()
            user_id = request.session.get('user_id')

            # 1. ENHANCED KNOWLEDGE BASE
            comparison_kb = """
            You are the EasyGo AI Assistant. 

            HOW TO BOOK A TRIP:
            1. Login: Click 'Login' in the top bar.
            2. Choose: Go to 'Packages' and click 'View Details'.
            3. Fill Form: Enter traveler names, ages, and choose Bus/Flight/Train.
            4. Pay: Use the QR code to pay via UPI.
            5. Confirm: Upload the payment screenshot to get your confirmed status.

            WHY EASYGO? (Comparison Table Data):
            - Group Discounts: 5% (6+), 10% (11+), 15% (21+).
            - Reviews: 100% Verified travelers only.
            - Waitlist: Auto-promotion system.
            """

            # 2. Database Context with Seat Counts and Waiting List
            packages = Package.objects.all()
            pkg_data = []

            for p in packages:
                # Get waiting list count for this package
                # Using waiting_list_position field instead of status
                waitlist_count = Booking.objects.filter(
                    package=p,
                    waiting_list_position__isnull=False  # Check if waiting_list_position is not null
                ).count() if hasattr(Booking, 'objects') else 0

                if p.available_seats > 0:
                    pkg_data.append(f"{p.title} (₹{p.base_price}) - Available Seats: {p.available_seats}")
                else:
                    pkg_data.append(f"{p.title} (₹{p.base_price}) - Waiting List Position: WL{waitlist_count + 1}")

            # Safe User Logic (Prevents the crash)
            user_name = "Guest"
            if user_id:
                user_obj = UserRegistration.objects.filter(id=user_id).first()
                if user_obj:
                    user_name = user_obj.name or "Traveler"

            # 3. Check for waiting list queries
            waiting_list_keywords = ['waiting list', 'waiting position', 'wl', 'waitlist', 'how many waiting']

            if any(keyword in user_message for keyword in waiting_list_keywords):
                # Extract package name from the query
                package_name = None
                # Check each package title to see if it's mentioned
                for package in packages:
                    if package.title.lower() in user_message or package.title.split()[0].lower() in user_message:
                        package_name = package.title
                        break

                if package_name:
                    package = Package.objects.filter(title=package_name).first()
                    if package:
                        # Count bookings with waiting_list_position
                        waitlist_count = Booking.objects.filter(
                            package=package,
                            waiting_list_position__isnull=False
                        ).count() if hasattr(Booking, 'objects') else 0

                        next_position = waitlist_count + 1

                        # Create HTML response with redirect guidance
                        html_response = f"""
                        <div class="alert alert-info">
                            <h5>📋 Waiting List Status for {package.title}</h5>
                            <p>Current waiting list position: <strong>WL{next_position}</strong></p>
                            <p>Total in queue: {waitlist_count} travelers</p>
                            <hr>
                            <p>🔍 To check exact availability or join the waiting list:</p>
                            <a href="/package-detail/{package.id}/" class="btn btn-primary btn-sm">
                                <i class="fa fa-eye"></i> View Package Details
                            </a>
                            <p class="mt-2 small text-muted">
                                The package details page shows real-time seat availability and waiting list positions.
                            </p>
                        </div>
                        """

                        # Also include text version for non-HTML responses
                        text_response = f"Waiting list position for {package.title} is WL{next_position}. {waitlist_count} people are currently in queue. Click here to view package details: /package-detail/{package.id}/"

                        return JsonResponse({
                            'reply': text_response,
                            'html_reply': html_response,
                            'package_id': package.id,
                            'redirect_url': f'/package-detail/{package.id}/'
                        })

                # If no specific package mentioned
                html_response = """
                <div class="alert alert-warning">
                    <h5>📋 Waiting List Information</h5>
                    <p>Please specify which package you'd like to check the waiting list for.</p>
                    <p>Available packages with their current status:</p>
                    <ul class="list-unstyled">
                """

                for p in packages[:5]:  # Show first 5 packages
                    if p.available_seats > 0:
                        html_response += f"<li>✅ {p.title} - {p.available_seats} seats available</li>"
                    else:
                        waitlist = Booking.objects.filter(
                            package=p,
                            waiting_list_position__isnull=False
                        ).count() if hasattr(Booking, 'objects') else 0
                        html_response += f"<li>⏳ {p.title} - WL{waitlist + 1} (next position)</li>"

                html_response += """
                    </ul>
                    <p class="mt-2">
                        <a href="/packages/" class="btn btn-primary btn-sm">
                            <i class="fa fa-search"></i> View All Packages
                        </a>
                    </p>
                </div>
                """

                return JsonResponse({
                    'reply': "Please specify which package you want to check the waiting list for. For example: 'What's the waiting list for Manali package?'",
                    'html_reply': html_response,
                    'redirect_url': '/packages/'
                })

            # 4. Regular Model Logic
            try:
                model = genai.GenerativeModel('gemini-2.5-flash')
            except:
                model = genai.GenerativeModel('gemini-1.5-flash')

            # Add guidance about redirects in the prompt
            prompt = (
                f"Instructions: {comparison_kb}\n"
                f"Packages: {pkg_data}\n"
                f"User: {user_name}\n"
                f"Query: {user_message}\n"
                f"Rule: If comparing, use a Markdown Table. If listing trips, show Available Seats.\n"
                f"When users ask about specific packages, encourage them to visit the package detail page for real-time updates."
            )

            response = model.generate_content(prompt)

            # Add redirect URLs to the response if package mentioned
            response_data = {'reply': response.text}

            # Check if response mentions a specific package and add redirect guidance
            for package in packages:
                if package.title.lower() in user_message or package.title.split()[0].lower() in user_message:
                    response_data['package_id'] = package.id
                    response_data['redirect_url'] = f'/package-detail/{package.id}/'
                    response_data['html_reply'] = f"""
                    {response.text}
                    <div class="mt-3">
                        <a href="/package-detail/{package.id}/" class="btn btn-outline-primary">
                            <i class="fa fa-arrow-right"></i> View {package.title} Details
                        </a>
                    </div>
                    """
                    break

            return JsonResponse(response_data)

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            return JsonResponse({'reply': "I'm ready to help! Ask me about our 'Best Packages' or how to book a trip."})

    return JsonResponse({'error': 'Invalid request'}, status=400)




def forgot_password(request):
    if request.method == "POST":
        email = request.POST.get('email')
        user = UserRegistration.objects.filter(email=email).first()

        if user:
            otp = str(random.randint(100000, 999999))
            request.session['reset_otp'] = otp
            request.session['reset_email'] = email

            # Send Email
            subject = "Password Reset OTP - Travela"
            message = f"Hello {user.name},\n\nYour OTP to reset your password is: {otp}"
            send_mail(subject, message, settings.EMAIL_HOST_USER, [email])

            messages.info(request, "OTP sent to your email.")
            return redirect('reset_password')
        else:
            messages.error(request, "No account found with this email.")

    return render(request, "forgot_password.html")


def reset_password(request):
    if request.method == "POST":
        user_otp = request.POST.get('otp')
        new_password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')

        saved_otp = request.session.get('reset_otp')
        email = request.session.get('reset_email')

        if user_otp != saved_otp:
            messages.error(request, "Invalid OTP.")
            return render(request, "reset_password.html")

        if new_password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, "reset_password.html")

        # Update Password
        user = UserRegistration.objects.get(email=email)
        user.password = new_password
        user.save()

        # Cleanup
        del request.session['reset_otp']
        del request.session['reset_email']

        messages.success(request, "Password reset successfully! Please login.")
        return redirect('login')

    return render(request, "reset_password.html")