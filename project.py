import os
import random
import string
from sqlalchemy import create_engine, asc
from sqlalchemy.orm import sessionmaker

from flask import Flask, render_template, request, redirect, jsonify, url_for, flash, make_response
from flask import session as login_session
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError
import httplib2
import json
import requests
from database_setup import Base, Restaurant, MenuItem, User

app = Flask(__name__)

# Connect to Database and create database session
db_path = os.path.join(os.path.dirname(__file__), 'restaurant_menu_with_users.db')
engine = create_engine('sqlite:///{}'.format(db_path))
Base.metadata.bind = engine

DBSession = sessionmaker(bind=engine)
session = DBSession()


def csrf_token():
    """Assigns a new token to session state and returns it"""
    token = ''.join(random.choice(string.ascii_uppercase + string.digits) for x in xrange(32))
    login_session['state'] = token
    return token


def create_user(login_session):
    new_user = User(name=login_session['username'], email=login_session['email'], picture=login_session['picture'])
    session.add(new_user)
    session.commit()
    user = session.query(User).filter_by(email=login_session['email']).first()
    return user.id


def get_user_info(user_id):
    return session.query(User).filter_by(id=user_id).first()


def get_user_id(email):
    user = session.query(User).filter_by(email=email).first()
    return user.id if user else None


def is_logged_in():
    return True if login_session.get('user_id') else False


@app.route('/login')
def show_login():
    return render_template("login.html", state=csrf_token(), logged_in=is_logged_in())


@app.route('/gconnect', methods=['POST'])
def gconnect():
    # Check CSRF token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter value!'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Get authorization code
    code = request.data

    # Try to upgrade from the authorization code to an access token
    try:
        oauth_flow = flow_from_clientsecrets('client_secrets.json', scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(json.dumps('Failed to exchange tokens!'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Check that the access token is valid
    access_token = credentials.access_token
    url = 'https://www.googleapis.com/oauth2/v1/tokeninfo?access_token={}'.format(access_token)
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])

    # If tokeninfo has an error, abort
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'

    # Verify that the token is for the intended user
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(json.dumps('Token\'s user id doesn\'t match given user id'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Verify that the token is valid for this app
    client_id = json.loads(open('client_secrets.json', 'r').read())['web']['client_id']
    if result['issued_to'] != client_id:
        response = make_response(json.dumps('Token\'s client id doesn\'t match app\'s'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Check to see if user is already logged in
    stored_credentials = login_session.get('credentials')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_credentials is not None and stored_gplus_id == gplus_id:
        response = make_response(json.dumps('Current user is already logged in', 200))
        response.headers['Content-Type'] = 'application/json'

    # Store credentials for later use
    login_session['credentials'] = credentials.to_json()
    login_session['gplus_id'] = gplus_id

    # Get user info
    userinfo_url = 'https://www.googleapis.com/oauth2/v2/userinfo'
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)
    data = json.loads(answer.text)

    # Store user info
    login_session['username'] = data['name']
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']

    # Check if user already exists
    user_id = get_user_id(login_session['email'])
    if not user_id:
        user_id = create_user(login_session)

    # Store user id
    login_session['user_id'] = user_id

    flash('Logged in as {}'.format(login_session['username']), category='success')

    response = make_response(json.dumps('Welcome {}'.format(login_session['username']), 200))
    response.headers['Content-Type'] = 'application/json'

    return response


@app.route('/gdisconnect')
def gdisconnect():
    # Check if user is connected
    if not login_session.get('credentials'):
        flash('User is not connected.', category='error')
        return redirect('/')

    credentials = json.loads(login_session.get('credentials'))

    # Revoke current token
    access_token = credentials['access_token']
    url = 'https://accounts.google.com/o/oauth2/revoke?token={}'.format(access_token)
    h = httplib2.Http()
    result = h.request(url, 'GET')[0]

    if result['status'] == '200':
        # Destroy user session
        del login_session['user_id']
        del login_session['credentials']
        del login_session['username']
        del login_session['gplus_id']
        del login_session['picture']
        del login_session['email']
        flash('Succcessfully disconnected', category='success')
        return redirect('/')
    else:
        flash('Failed to revoke token', category='error')
        return redirect('/')


# JSON APIs to view Restaurant Information
@app.route('/restaurant/<int:restaurant_id>/menu/JSON')
def restaurantMenuJSON(restaurant_id):
    restaurant = session.query(Restaurant).filter_by(id=restaurant_id).one()
    items = session.query(MenuItem).filter_by(restaurant_id=restaurant_id).all()
    return jsonify(MenuItems=[i.serialize for i in items])


@app.route('/restaurant/<int:restaurant_id>/menu/<int:menu_id>/JSON')
def menuItemJSON(restaurant_id, menu_id):
    Menu_Item = session.query(MenuItem).filter_by(id=menu_id).one()
    return jsonify(Menu_Item=Menu_Item.serialize)


@app.route('/restaurant/JSON')
def restaurantsJSON():
    restaurants = session.query(Restaurant).all()
    return jsonify(restaurants=[r.serialize for r in restaurants])


# Show all restaurants
@app.route('/')
@app.route('/restaurant/')
def showRestaurants():
    restaurants = session.query(Restaurant).order_by(asc(Restaurant.name))

    if login_session.get('user_id'):
        return render_template('restaurants.html', restaurants=restaurants,
                               logged_in=is_logged_in())
    else:
        return render_template('publicRestaurants.html', restaurants=restaurants,
                               logged_in=is_logged_in())


# Create a new restaurant
@app.route('/restaurant/new/', methods=['GET', 'POST'])
def newRestaurant():
    if login_session.get('username') is None:
        return redirect('/login')
    if request.method == 'POST':
        newRestaurant = Restaurant(name=request.form['name'], user_id=login_session['user_id'])
        session.add(newRestaurant)
        flash('New Restaurant {} Successfully Created'.format(newRestaurant.name), category='success')
        session.commit()
        return redirect(url_for('showRestaurants'))
    else:
        return render_template('newRestaurant.html', logged_in=is_logged_in())


# Edit a restaurant
@app.route('/restaurant/<int:restaurant_id>/edit/', methods=['GET', 'POST'])
def editRestaurant(restaurant_id):
    if login_session.get('username') is None:
        return redirect('/login')
    editedRestaurant = session.query(Restaurant).filter_by(id=restaurant_id).one()
    if request.method == 'POST':
        if request.form['name']:
            editedRestaurant.name = request.form['name']
            flash('Restaurant Successfully Edited {}'.format(editedRestaurant.name), category='success')
            return redirect(url_for('showRestaurants'))
    else:
        return render_template('editRestaurant.html', restaurant=editedRestaurant,
                               logged_in=is_logged_in())


# Delete a restaurant
@app.route('/restaurant/<int:restaurant_id>/delete/', methods=['GET', 'POST'])
def deleteRestaurant(restaurant_id):
    if login_session.get('username') is None:
        return redirect('/login')
    restaurantToDelete = session.query(Restaurant).filter_by(id=restaurant_id).one()
    if request.method == 'POST':
        session.delete(restaurantToDelete)
        flash('{} Successfully Deleted'.format(restaurantToDelete.name), category='success')
        session.commit()
        return redirect(url_for('showRestaurants', restaurant_id=restaurant_id))
    else:
        return render_template('deleteRestaurant.html', restaurant=restaurantToDelete,
                               logged_in=is_logged_in())


# Show a restaurant menu
@app.route('/restaurant/<int:restaurant_id>/')
@app.route('/restaurant/<int:restaurant_id>/menu/')
def showMenu(restaurant_id):
    restaurant = session.query(Restaurant).filter_by(id=restaurant_id).one()
    items = session.query(MenuItem).filter_by(restaurant_id=restaurant_id).all()

    if is_logged_in():
        return render_template('menu.html', items=items, restaurant=restaurant, logged_in=is_logged_in())
    else:
        return render_template('publicMenu.html', items=items, restaurant=restaurant, logged_in=is_logged_in())


# Create a new menu item
@app.route('/restaurant/<int:restaurant_id>/menu/new/', methods=['GET', 'POST'])
def newMenuItem(restaurant_id):
    if login_session.get('username') is None:
        return redirect('/login')
    restaurant = session.query(Restaurant).filter_by(id=restaurant_id).one()
    if request.method == 'POST':
        newItem = MenuItem(name=request.form['name'], description=request.form['description'],
                           price=request.form['price'], course=request.form['course'],
                           restaurant_id=restaurant_id, user_id=login_session['user_id'])
        session.add(newItem)
        session.commit()
        flash('New Menu {} Item Successfully Created'.format(newItem.name), category='success')
        return redirect(url_for('showMenu', restaurant_id=restaurant_id))
    else:
        return render_template('newmenuitem.html', restaurant_id=restaurant_id,
                               logged_in=is_logged_in())


# Edit a menu item
@app.route('/restaurant/<int:restaurant_id>/menu/<int:menu_id>/edit', methods=['GET', 'POST'])
def editMenuItem(restaurant_id, menu_id):
    if login_session.get('username') is None:
        return redirect('/login')
    editedItem = session.query(MenuItem).filter_by(id=menu_id).one()
    restaurant = session.query(Restaurant).filter_by(id=restaurant_id).one()
    if request.method == 'POST':
        if request.form['name']:
            editedItem.name = request.form['name']
        if request.form['description']:
            editedItem.description = request.form['description']
        if request.form['price']:
            editedItem.price = request.form['price']
        if request.form['course']:
            editedItem.course = request.form['course']
        session.add(editedItem)
        session.commit()
        flash('Menu Item Successfully Edited', category='success')
        return redirect(url_for('showMenu', restaurant_id=restaurant_id))
    else:
        return render_template('editmenuitem.html', restaurant_id=restaurant_id, menu_id=menu_id, item=editedItem,
                               logged_in=is_logged_in())


# Delete a menu item
@app.route('/restaurant/<int:restaurant_id>/menu/<int:menu_id>/delete', methods=['GET', 'POST'])
def deleteMenuItem(restaurant_id, menu_id):
    if login_session.get('username') is None:
        return redirect('/login')
    restaurant = session.query(Restaurant).filter_by(id=restaurant_id).one()
    itemToDelete = session.query(MenuItem).filter_by(id=menu_id).one()
    if request.method == 'POST':
        session.delete(itemToDelete)
        session.commit()
        flash('Menu Item Successfully Deleted', category='success')
        return redirect(url_for('showMenu', restaurant_id=restaurant_id))
    else:
        return render_template('deleteMenuItem.html', item=itemToDelete,
                               logged_in=is_logged_in())


if __name__ == '__main__':
    app.secret_key = 'super_secret_key'
    app.debug = True
    app.run(host='0.0.0.0', port=5000)
